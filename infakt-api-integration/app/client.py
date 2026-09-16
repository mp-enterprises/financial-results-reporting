"""Klient REST API v3 inFakt.

Kształt paginacji potwierdzony na żywo (konto produkcyjne, 2026-09-16):
odpowiedź to `{"metainfo": {"count", "total_count", "next", "previous"},
"entities": [...]}`. To paginacja przez `offset`/`limit`, NIE `page`/
`total_pages` — serwer też potrafi zignorować żądany `limit` i zwrócić własny
rozmiar strony (np. invoices.json ograniczało do 10 mimo `limit=100`), więc
przesuwamy `offset` o faktyczną liczbę zwróconych rekordów, nie o żądany
rozmiar strony. `next`/`previous` bywają obecne nawet za granicą
`total_count`, więc NIE służą jako warunek stopu.

Limity zapytań: 300 GET / 60s, 150 POST/PUT/DELETE / 60s (potwierdzone w
oficjalnym README repo infakt/API).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import requests

logger = logging.getLogger(__name__)

BASE_URLS = {
    "production": "https://api.infakt.pl/api/v3",
    "sandbox": "https://api.sandbox-infakt.pl/api/v3",
}

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 500  # zabezpieczenie przed nieskończoną pętlą, gdyby total_count było nie do odczytania


class InfaktApiError(RuntimeError):
    """Błąd zwrócony przez API inFakt (status >= 400, poza obsłużonym 429)."""


class InfaktAuthError(InfaktApiError):
    """Brak lub nieprawidłowy klucz API (401/403)."""


class InfaktRateLimitError(InfaktApiError):
    """Przekroczono limit zapytań mimo ponowień."""


def _rozbierz_strone(payload: Any) -> tuple[list[dict], int | None]:
    """Wyciąga (lista_rekordów, total_count_lub_None) z odpowiedzi API."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        records: list[dict] | None = None
        for key in ("entities", "data", "results", "items"):
            wartosc = payload.get(key)
            if isinstance(wartosc, list):
                records = wartosc
                break
        if records is None:
            raise InfaktApiError(
                f"Nieoczekiwany kształt odpowiedzi inFakt — brak listy rekordów "
                f"pod znanymi kluczami (entities/data/results/items): {list(payload.keys())}"
            )
        total_count = None
        meta = payload.get("metainfo") or payload.get("metadata") or payload.get("pagination") or {}
        if isinstance(meta, dict):
            for key in ("total_count", "total_entries", "count_total"):
                if key in meta:
                    total_count = int(meta[key])
                    break
        return records, total_count
    raise InfaktApiError(f"Nieoczekiwany typ odpowiedzi inFakt: {type(payload)!r}")


@dataclass
class InfaktClient:
    api_key: str
    environment: str = "sandbox"
    base_url: str | None = None
    max_retries: int = 5
    timeout_s: float = 30.0
    session: requests.Session | None = None
    sleep_fn: Callable[[float], None] = field(default=time.sleep)

    def __post_init__(self) -> None:
        if not self.api_key:
            raise InfaktAuthError(
                "Brak INFAKT_API_KEY — uzupełnij .env kluczem z zakresami "
                "api:invoices:read, api:costs:read, api:accounting:read."
            )
        resolved_base = self.base_url or BASE_URLS.get(self.environment)
        if not resolved_base:
            raise ValueError(
                f"Nieznane środowisko inFakt: {self.environment!r} "
                f"(oczekiwano jednego z: {sorted(BASE_URLS)})"
            )
        self._base = resolved_base
        self._session = self.session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"X-inFakt-ApiKey": self.api_key, "Accept": "application/json"}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Pojedyncze zapytanie GET z ponowieniami przy 429."""
        url = f"{self._base}/{path.lstrip('/')}"
        attempt = 0
        while True:
            response = self._session.get(
                url, headers=self._headers(), params=params, timeout=self.timeout_s
            )
            if response.status_code == 429:
                attempt += 1
                if attempt > self.max_retries:
                    raise InfaktRateLimitError(
                        f"Limit zapytań przekroczony mimo {self.max_retries} ponowień: {url}"
                    )
                # inFakt bywa, że zwraca Retry-After: 0 — honorujemy nagłówek tylko gdy
                # każe czekać DŁUŻEJ niż nasz własny backoff; inaczej i tak natychmiast
                # trafiamy w ten sam limit i wyczerpujemy ponowienia bez żadnej zwłoki.
                backoff_wlasny = float(2 ** attempt)
                try:
                    retry_after = float(response.headers.get("Retry-After", 0))
                except ValueError:
                    retry_after = 0.0
                wait_s = max(backoff_wlasny, retry_after)
                logger.warning(
                    "429 z inFakt dla %s — ponowienie za %.1fs (próba %d/%d)",
                    url, wait_s, attempt, self.max_retries,
                )
                self.sleep_fn(wait_s)
                continue
            if response.status_code in (401, 403):
                raise InfaktAuthError(f"inFakt odrzucił klucz API ({response.status_code}) dla {url}")
            if response.status_code >= 400:
                raise InfaktApiError(
                    f"inFakt zwrócił {response.status_code} dla {url}: {response.text[:500]}"
                )
            return response.json()

    def get_all_pages(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> Iterator[dict[str, Any]]:
        """Generator rekordów ze wszystkich stron danego zasobu (paginacja przez offset/limit)."""
        base_params = dict(params or {})
        offset = 0
        for strona in range(1, max_pages + 1):
            payload = self.get(path, params={**base_params, "offset": offset, "limit": page_size})
            records, total_count = _rozbierz_strone(payload)
            yield from records
            if not records:
                return
            offset += len(records)
            if total_count is not None:
                if offset >= total_count:
                    return
            elif len(records) < page_size:
                # total_count nieznane (np. odpowiedź to goła lista) — jedyny sygnał
                # końca to strona krótsza niż żądany rozmiar.
                return
        raise InfaktApiError(
            f"Przekroczono limit {max_pages} stron dla {path} bez osiągnięcia końca "
            f"(total_count nieznane lub nieprawdziwe) — możliwa pętla paginacji"
        )
