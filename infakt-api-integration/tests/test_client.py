from __future__ import annotations

import pytest
import responses

from app.client import (
    BASE_URLS,
    InfaktApiError,
    InfaktAuthError,
    InfaktClient,
    InfaktRateLimitError,
)


def test_brak_klucza_api_podnosi_blad():
    with pytest.raises(InfaktAuthError):
        InfaktClient(api_key="")


def test_nieznane_srodowisko_podnosi_blad():
    with pytest.raises(ValueError):
        InfaktClient(api_key="klucz", environment="nieistniejace")


@responses.activate
def test_get_wysyla_naglowek_autoryzacji(client: InfaktClient):
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json=[{"id": 1}],
        status=200,
    )
    client.get("invoices.json")
    assert responses.calls[0].request.headers["X-inFakt-ApiKey"] == "test-klucz"


@responses.activate
def test_401_podnosi_blad_autoryzacji(client: InfaktClient):
    responses.add(
        responses.GET, f"{BASE_URLS['sandbox']}/invoices.json", json={"error": "unauthorized"}, status=401
    )
    with pytest.raises(InfaktAuthError):
        client.get("invoices.json")


@responses.activate
def test_500_podnosi_ogolny_blad_api(client: InfaktClient):
    responses.add(
        responses.GET, f"{BASE_URLS['sandbox']}/invoices.json", json={"error": "boom"}, status=500
    )
    with pytest.raises(InfaktApiError):
        client.get("invoices.json")


@responses.activate
def test_429_ponawia_i_konczy_sukcesem(client: InfaktClient):
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"error": "rate limited"},
        status=429,
        headers={"Retry-After": "0"},
    )
    responses.add(
        responses.GET, f"{BASE_URLS['sandbox']}/invoices.json", json=[{"id": 1}], status=200
    )
    wynik = client.get("invoices.json")
    assert wynik == [{"id": 1}]
    assert len(responses.calls) == 2


@responses.activate
def test_429_ignoruje_retry_after_zero_i_stosuje_wlasny_backoff():
    """Regresja: inFakt bywa, że zwraca Retry-After: 0 — bez podłogi backoffu
    to prowadzi do natychmiastowych ponowień i wyczerpania limitu w ułamku sekundy."""
    opoznienia: list[float] = []
    client = InfaktClient(
        api_key="test-klucz",
        environment="sandbox",
        max_retries=2,
        sleep_fn=opoznienia.append,
    )
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"error": "rate limited"},
        status=429,
        headers={"Retry-After": "0"},
    )
    responses.add(
        responses.GET, f"{BASE_URLS['sandbox']}/invoices.json", json=[{"id": 1}], status=200
    )
    client.get("invoices.json")
    assert opoznienia == [2.0]  # 2**1, nie 0.0 z nagłówka


@responses.activate
def test_429_wyczerpanie_ponowien_podnosi_blad(client: InfaktClient):
    for _ in range(client.max_retries + 1):
        responses.add(
            responses.GET,
            f"{BASE_URLS['sandbox']}/invoices.json",
            json={"error": "rate limited"},
            status=429,
            headers={"Retry-After": "0"},
        )
    with pytest.raises(InfaktRateLimitError):
        client.get("invoices.json")


@responses.activate
def test_get_all_pages_odpowiedz_jako_lista_jedna_strona(client: InfaktClient):
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json=[{"id": 1}, {"id": 2}],
        status=200,
    )
    rekordy = list(client.get_all_pages("invoices.json", page_size=100))
    assert rekordy == [{"id": 1}, {"id": 2}]
    assert len(responses.calls) == 1


@responses.activate
def test_get_all_pages_paginacja_przez_metainfo(client: InfaktClient):
    """Kształt potwierdzony na żywo: metainfo.total_count + offset/limit, nie page/total_pages."""
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"entities": [{"id": 1}], "metainfo": {"count": 1, "total_count": 2}},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"entities": [{"id": 2}], "metainfo": {"count": 1, "total_count": 2}},
        status=200,
    )
    rekordy = list(client.get_all_pages("invoices.json", page_size=1))
    assert [r["id"] for r in rekordy] == [1, 2]
    assert len(responses.calls) == 2
    assert responses.calls[0].request.params["offset"] == "0"
    assert responses.calls[1].request.params["offset"] == "1"


@responses.activate
def test_get_all_pages_ignoruje_limit_zwrocony_przez_serwer(client: InfaktClient):
    """Regresja: serwer bywa, że zwraca mniej rekordów niż żądany `limit` (np. invoices.json
    ograniczało do 10 mimo limit=100) — offset musi się przesuwać o realną liczbę rekordów."""
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"entities": [{"id": 1}, {"id": 2}], "metainfo": {"count": 2, "total_count": 3}},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"entities": [{"id": 3}], "metainfo": {"count": 1, "total_count": 3}},
        status=200,
    )
    rekordy = list(client.get_all_pages("invoices.json", page_size=100))
    assert [r["id"] for r in rekordy] == [1, 2, 3]
    assert responses.calls[1].request.params["offset"] == "2"


@responses.activate
def test_get_all_pages_przekroczenie_max_pages_podnosi_blad(client: InfaktClient):
    """Bez total_count i bez strony krótszej niż page_size — zabezpieczenie przed pętlą."""
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"entities": [{"id": 1}]},  # brak metainfo -> total_count nieznane
        status=200,
    )
    with pytest.raises(InfaktApiError):
        list(client.get_all_pages("invoices.json", page_size=1, max_pages=3))


@responses.activate
def test_get_all_pages_paginacja_po_niepelnej_stronie(client: InfaktClient):
    """Brak metadanych paginacji — koniec rozpoznajemy po stronie krótszej niż page_size."""
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"data": [{"id": 1}, {"id": 2}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URLS['sandbox']}/invoices.json",
        json={"data": [{"id": 3}]},
        status=200,
    )
    rekordy = list(client.get_all_pages("invoices.json", page_size=2))
    assert [r["id"] for r in rekordy] == [1, 2, 3]
    assert len(responses.calls) == 2


@responses.activate
def test_get_all_pages_nieoczekiwany_ksztalt_podnosi_blad(client: InfaktClient):
    responses.add(
        responses.GET, f"{BASE_URLS['sandbox']}/invoices.json", json={"foo": "bar"}, status=200
    )
    with pytest.raises(InfaktApiError):
        list(client.get_all_pages("invoices.json"))
