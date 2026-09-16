"""Wykluczanie kosztów po NIP dostawcy (denylist po stronie klienta).

Patrz README.md, sekcja "Faktury kosztowe z wyłączeniem konkretnych
dostawców": natywne filtrowanie/wykluczanie po NIP sprzedawcy w
documents/costs.json NIE jest potwierdzone w oficjalnej dokumentacji, więc
pobieramy wszystkie koszty za okres i filtrujemy je tutaj, po stronie klienta.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Możliwe nazwy pola z NIP sprzedawcy w rekordzie kosztu — nieoficjalne SDK
# (przemekperon/infakt-go-sdk) sugeruje SellerTaxCode, ale to wnioskowanie,
# nie potwierdzony fakt. Do zweryfikowania w sandboxie.
POLA_NIP_SPRZEDAWCY: tuple[str, ...] = (
    "seller_tax_code_number",
    "seller_nip",
    "seller_tax_code",
    "contractor_tax_code_number",
    "kontrahent_nip",
)


def normalizuj_nip(nip: str) -> str:
    """Usuwa spacje/myślniki, zostawia same cyfry — NIP jako identyfikator, nie tekst."""
    return re.sub(r"\D", "", nip or "")


def wczytaj_denylist(surowa_lista: str) -> set[str]:
    """surowa_lista: NIP-y rozdzielone przecinkiem/średnikiem/białym znakiem (np. z env)."""
    if not surowa_lista or not surowa_lista.strip():
        return set()
    fragmenty = re.split(r"[,;\s]+", surowa_lista.strip())
    return {normalizuj_nip(f) for f in fragmenty if f}


def wyciagnij_nip_sprzedawcy(koszt: dict) -> str | None:
    for pole in POLA_NIP_SPRZEDAWCY:
        wartosc = koszt.get(pole)
        if wartosc:
            return normalizuj_nip(str(wartosc))
    return None


def odfiltruj_koszty(
    koszty: list[dict], denylist: set[str]
) -> tuple[list[dict], list[dict], int]:
    """
    Zwraca (dopuszczone, odrzucone, liczba_bez_rozpoznanego_nip).

    Rekordy, w których nie udało się odnaleźć NIP sprzedawcy w żadnym ze
    znanych pól, są DOPUSZCZANE (fail-open) — pole z NIP sprzedawcy nie jest
    potwierdzone w oficjalnej dokumentacji API, więc milczące odrzucanie
    kosztu tylko dlatego, że nie rozpoznaliśmy pola, byłoby ryzykowne
    (zaniżyłoby koszty bez ostrzeżenia). Liczbę takich rekordów zwracamy
    osobno, żeby dało się to zauważyć w podsumowaniu.
    """
    if not denylist:
        return list(koszty), [], 0
    dopuszczone: list[dict] = []
    odrzucone: list[dict] = []
    bez_nip = 0
    for koszt in koszty:
        nip = wyciagnij_nip_sprzedawcy(koszt)
        if nip is None:
            bez_nip += 1
            dopuszczone.append(koszt)
        elif nip in denylist:
            odrzucone.append(koszt)
        else:
            dopuszczone.append(koszt)
    if bez_nip:
        logger.warning(
            "%d kosztów bez rozpoznanego NIP sprzedawcy — przepuszczone bez filtrowania denylistą",
            bez_nip,
        )
    return dopuszczone, odrzucone, bez_nip
