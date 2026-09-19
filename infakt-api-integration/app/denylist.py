"""Wykluczanie rekordów po NIP kontrahenta (denylist po stronie klienta).

Ta sama lista NIP-ów (`INFAKT_SUPPLIER_DENYLIST_NIP`) jest stosowana po obu
stronach: jako dostawca w kosztach (`documents/costs.json`) i jako klient w
fakturach przychodowych (`invoices.json`) — chodzi o wykluczenie wszelkich
rozliczeń z danym podmiotem, niezależnie od kierunku transakcji.

Patrz README.md, sekcja "Faktury kosztowe z wyłączeniem konkretnych
dostawców": natywne filtrowanie/wykluczanie po NIP w API v3 NIE jest
potwierdzone w oficjalnej dokumentacji, więc pobieramy wszystkie rekordy za
okres i filtrujemy je tutaj, po stronie klienta.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

# Możliwe nazwy pola z NIP sprzedawcy w rekordzie kosztu — nieoficjalne SDK
# (przemekperon/infakt-go-sdk) sugerowało SellerTaxCode; potwierdzone na żywo
# (2026-09-17): to `seller_tax_code`.
POLA_NIP_SPRZEDAWCY: tuple[str, ...] = (
    "seller_tax_code_number",
    "seller_nip",
    "seller_tax_code",
    "contractor_tax_code_number",
    "kontrahent_nip",
)

# Możliwe nazwy pola z NIP klienta (nabywcy) w rekordzie faktury przychodowej.
# Potwierdzone na żywo (2026-09-18): to `client_tax_code`.
POLA_NIP_KLIENTA: tuple[str, ...] = (
    "client_tax_code_number",
    "client_nip",
    "client_tax_code",
    "buyer_tax_code_number",
    "nabywca_nip",
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


def wyciagnij_nip(rekord: dict, kandydaci: tuple[str, ...]) -> str | None:
    for pole in kandydaci:
        wartosc = rekord.get(pole)
        if wartosc:
            return normalizuj_nip(str(wartosc))
    return None


def wyciagnij_nip_sprzedawcy(koszt: dict) -> str | None:
    return wyciagnij_nip(koszt, POLA_NIP_SPRZEDAWCY)


def wyciagnij_nip_klienta(faktura: dict) -> str | None:
    return wyciagnij_nip(faktura, POLA_NIP_KLIENTA)


def odfiltruj_wedlug_nip(
    rekordy: list[dict], denylist: set[str], wyciagacz: Callable[[dict], str | None]
) -> tuple[list[dict], list[dict], int]:
    """
    Zwraca (dopuszczone, odrzucone, liczba_bez_rozpoznanego_nip).

    Rekordy, w których nie udało się odnaleźć NIP kontrahenta w żadnym ze
    znanych pól, są DOPUSZCZANE (fail-open) — pole z NIP nie jest
    potwierdzone w oficjalnej dokumentacji API, więc milczące odrzucanie
    rekordu tylko dlatego, że nie rozpoznaliśmy pola, byłoby ryzykowne
    (zaniżyłoby koszty/przychód bez ostrzeżenia). Liczbę takich rekordów
    zwracamy osobno, żeby dało się to zauważyć w podsumowaniu.
    """
    if not denylist:
        return list(rekordy), [], 0
    dopuszczone: list[dict] = []
    odrzucone: list[dict] = []
    bez_nip = 0
    for rekord in rekordy:
        nip = wyciagacz(rekord)
        if nip is None:
            bez_nip += 1
            dopuszczone.append(rekord)
        elif nip in denylist:
            odrzucone.append(rekord)
        else:
            dopuszczone.append(rekord)
    if bez_nip:
        logger.warning(
            "%d rekordów bez rozpoznanego NIP kontrahenta — przepuszczone bez filtrowania denylistą",
            bez_nip,
        )
    return dopuszczone, odrzucone, bez_nip


def odfiltruj_koszty(
    koszty: list[dict], denylist: set[str]
) -> tuple[list[dict], list[dict], int]:
    """Wykluczenie kosztów wg NIP sprzedawcy (dostawcy)."""
    return odfiltruj_wedlug_nip(koszty, denylist, wyciagnij_nip_sprzedawcy)


def odfiltruj_faktury(
    faktury: list[dict], denylist: set[str]
) -> tuple[list[dict], list[dict], int]:
    """Wykluczenie faktur przychodowych wg NIP klienta (nabywcy)."""
    return odfiltruj_wedlug_nip(faktury, denylist, wyciagnij_nip_klienta)
