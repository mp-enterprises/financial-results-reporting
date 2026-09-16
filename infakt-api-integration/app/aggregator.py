"""Wyliczenia odtwarzające karty panelu Statystyki inFakt.

Wzór podany wprost w artykule pomocy inFakt o panelu Statystyki:
    zysk/strata = przychód − (koszty + podatek dochodowy + ZUS)

Patrz README.md, sekcja "Co pokazuje panel Statystyk", po pełne mapowanie i
zastrzeżenia (m.in. rozróżnienie KPiR/ryczałt dla amortyzacji nieopisane w
polu API — nieuwzględnione tutaj).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# Kolejność kandydatów ma znaczenie — pierwsze trafione pole wygrywa.
#
# Świadomie NIE ma tu "sum_amount_price" (pole potwierdzone na żywo w
# insurance_fees.json 2026-09-16) — jego skala jest niejasna: jeden rekord
# miał sum_amount_price=9233, co nie pasuje przekonująco ani jako 92.33 PLN
# (w groszach) ani jako 9233 PLN za pojedynczy składnik ZUS, a 18 rekordów na
# raptem kilka miesięcy sugeruje, że to sumy per SKŁADNIK ZUS, nie per okres.
# Dodanie go bez weryfikacji ryzykowałoby cichym zepsuciem wyliczenia o
# nieznaną skalę — bezpieczniejsze jest zostawić ZUS jako 0.00 z jawnym
# ostrzeżeniem (patrz README) niż zgadywać. Do zweryfikowania w sandboxie.
POLA_KWOTY_BRUTTO: tuple[str, ...] = ("gross_price", "gross_amount", "total_price_gross", "amount")
GROSZE_DZIELNIK = Decimal("100")
DWA_MIEJSCA = Decimal("0.01")


def _pieniadze(kwota: Decimal) -> str:
    """Zawsze dwa miejsca po przecinku w wyjściu — Decimal potrafi zgubić końcowe zera."""
    return str(kwota.quantize(DWA_MIEJSCA, rounding=ROUND_HALF_UP))


def _do_decimal(wartosc: Any) -> Decimal:
    if wartosc is None:
        return Decimal("0")
    try:
        return Decimal(str(wartosc))
    except InvalidOperation:
        return Decimal("0")


def wyciagnij_kwote(
    rekord: dict, kandydaci: tuple[str, ...] = POLA_KWOTY_BRUTTO, w_groszach: bool = True
) -> Decimal:
    """
    Kwoty w API v3 inFakt są wyrażone w groszach dla faktur sprzedażowych
    (potwierdzone w oficjalnym README) — DO ZWERYFIKOWANIA w sandboxie dla
    documents/costs.json oraz zasobów podatkowych/ZUS (patrz README.md).
    """
    for pole in kandydaci:
        if pole in rekord and rekord[pole] is not None:
            kwota = _do_decimal(rekord[pole])
            return kwota / GROSZE_DZIELNIK if w_groszach else kwota
    return Decimal("0")


def zsumuj(rekordy: list[dict], kandydaci: tuple[str, ...] = POLA_KWOTY_BRUTTO) -> Decimal:
    return sum((wyciagnij_kwote(r, kandydaci) for r in rekordy), Decimal("0"))


def zbuduj_podsumowanie(
    faktury: dict[str, list[dict]],
    koszty: list[dict],
    skladki_zus: list[dict],
    podatki_dochodowe: list[dict],
) -> dict[str, Any]:
    przychod = sum((zsumuj(rekordy) for rekordy in faktury.values()), Decimal("0"))
    suma_kosztow = zsumuj(koszty)
    suma_zus = zsumuj(skladki_zus)
    suma_podatku = zsumuj(podatki_dochodowe)
    zysk = przychod - (suma_kosztow + suma_podatku + suma_zus)
    return {
        "przychod": _pieniadze(przychod),
        "koszty": _pieniadze(suma_kosztow),
        "podatek_dochodowy": _pieniadze(suma_podatku),
        "zus": _pieniadze(suma_zus),
        "zysk_strata": _pieniadze(zysk),
        "liczba_faktur_przychodowych": sum(len(v) for v in faktury.values()),
        "liczba_kosztow": len(koszty),
        "rozbicie_przychodu": {nazwa: _pieniadze(zsumuj(rekordy)) for nazwa, rekordy in faktury.items()},
    }
