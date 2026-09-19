"""Zasoby API v3 potrzebne do replikacji panelu Statystyki inFakt.

Mapowanie karta panelu -> zasoby API — patrz README.md, sekcja "Co pokazuje
panel Statystyk i mapowanie na API".
"""
from __future__ import annotations

from typing import Any

from .client import InfaktClient

# --- przychód: spółka wystawia wyłącznie faktury VAT — to jedyny typ zdarzenia
#     sprzedażowego, więc pozostałe zasoby faktur (margin/advance/final/oss/
#     internal) świadomie nie są tu pobierane. -----------------
FAKTURY_PRZYCHODOWE: dict[str, str] = {
    "invoices": "invoices.json",
}

# --- koszty ------------------------------------------------------------------
KOSZTY: dict[str, str] = {
    "costs": "documents/costs.json",
}

# --- podatki i ZUS -------------------------------------------------------------
PODATKI_I_ZUS: dict[str, str] = {
    "insurance_fees": "insurance_fees.json",  # ZUS
    "income_taxes": "income_taxes.json",  # PIT/CIT
    "saf_v7_files": "saf_v7_files.json",  # JPK V7 (VAT)
}

WSZYSTKIE_ZASOBY: dict[str, str] = {**FAKTURY_PRZYCHODOWE, **KOSZTY, **PODATKI_I_ZUS}

# Pole z datą dokumentu, potwierdzone na żywo (konto produkcyjne, 2026-09-16)
# dla invoices/costs. Dla insurance_fees/income_taxes/saf_v7_files pole daty
# jest NIEZNANE — te trzy zasoby nie są filtrowane po stronie klienta
# (patrz README, sekcja "Ograniczenia").
POLA_DATY: dict[str, str] = {
    "invoices": "invoice_date",
    "costs": "issue_date",
}


def zbuduj_filtr_daty(od: str, do: str, pole: str = "invoice_date") -> dict[str, Any]:
    """
    od/do w formacie YYYY-MM-DD (do wyłącznie). Wysyłane do API jako
    najlepsze możliwe podpowiedzenie serwerowi — NIE gwarantuje filtrowania
    (potwierdzone na żywo: documents/costs.json ignoruje ten parametr w
    obu wariantach, płaskim i zagnieżdżonym `q[...]`). Właściwe filtrowanie
    robimy zawsze także po stronie klienta, patrz `w_okresie`/`filtruj_po_okresie`.
    """
    return {f"{pole}_gteq": od, f"{pole}_lt": do}


def w_okresie(rekord: dict, pole: str, od: str, do: str) -> bool:
    """od/do w formacie YYYY-MM-DD (do wyłącznie); porównanie leksykograficzne działa dla ISO 8601."""
    wartosc = rekord.get(pole)
    return wartosc is not None and od <= str(wartosc)[:10] < do


def filtruj_po_okresie(rekordy: list[dict], pole: str, od: str, do: str) -> tuple[list[dict], int]:
    """Zwraca (rekordy_w_okresie, liczba_bez_rozpoznanej_daty)."""
    w_zakresie = [r for r in rekordy if w_okresie(r, pole, od, do)]
    bez_daty = sum(1 for r in rekordy if r.get(pole) is None)
    return w_zakresie, bez_daty


# Pole `source` w documents/costs.json, potwierdzone na żywo (konto produkcyjne,
# 2026-09-17): wartość "ksef" oznacza koszt wczytany przez Krajowy System
# e-Faktur, "infakt" — dodany ręcznie/inną drogą w panelu. Tylko koszty źródła
# KSeF są uznawane za wiarygodne dla rozliczeń — patrz README.
ZRODLO_KSEF = "ksef"


def filtruj_zrodlo_ksef(koszty: list[dict]) -> list[dict]:
    """Zwraca tylko koszty, których pole `source` == "ksef"."""
    return [k for k in koszty if k.get("source") == ZRODLO_KSEF]


# invoices.json nie ma pola `source` — obecność numeru KSeF w polu `ksef_number`
# (potwierdzone na żywo, 2026-09-18) jest tu odpowiednikiem: niepuste oznacza,
# że faktura została zaraportowana do Krajowego Systemu e-Faktur.
POLE_KSEF_FAKTURY = "ksef_number"


def filtruj_ksef_faktury(faktury: list[dict]) -> list[dict]:
    """Zwraca tylko faktury przychodowe z niepustym numerem KSeF."""
    return [f for f in faktury if f.get(POLE_KSEF_FAKTURY)]


def pobierz_zasob(
    client: InfaktClient, nazwa: str, params: dict[str, Any] | None = None
) -> list[dict]:
    """Pobiera wszystkie strony danego zasobu i zwraca listę surowych rekordów."""
    if nazwa not in WSZYSTKIE_ZASOBY:
        raise ValueError(
            f"Nieznany zasób inFakt: {nazwa!r} (dostępne: {sorted(WSZYSTKIE_ZASOBY)})"
        )
    return list(client.get_all_pages(WSZYSTKIE_ZASOBY[nazwa], params=params))
