from __future__ import annotations

from decimal import Decimal

from app.aggregator import wyciagnij_kwote, zbuduj_podsumowanie, zsumuj


def test_wyciagnij_kwote_konwertuje_z_groszy():
    assert wyciagnij_kwote({"gross_price": 123456}) == Decimal("1234.56")


def test_wyciagnij_kwote_probuje_kolejnych_kandydatow():
    assert wyciagnij_kwote({"amount": 500}) == Decimal("5.00")


def test_wyciagnij_kwote_brak_pola_zwraca_zero():
    assert wyciagnij_kwote({"id": 1}) == Decimal("0")


def test_wyciagnij_kwote_bez_konwersji_z_groszy():
    assert wyciagnij_kwote({"gross_price": 42}, w_groszach=False) == Decimal("42")


def test_zsumuj_sumuje_liste_rekordow():
    rekordy = [{"gross_price": 100}, {"gross_price": 200}]
    assert zsumuj(rekordy) == Decimal("3.00")


def test_zbuduj_podsumowanie_stosuje_wzor_zysku():
    faktury = {"invoices": [{"gross_price": 1000_00}], "margin_invoices": [{"gross_price": 500_00}]}
    koszty = [{"gross_price": 300_00}]
    zus = [{"gross_price": 100_00}]
    podatki = [{"gross_price": 50_00}]

    podsumowanie = zbuduj_podsumowanie(faktury, koszty, zus, podatki)

    assert podsumowanie["przychod"] == "1500.00"
    assert podsumowanie["koszty"] == "300.00"
    assert podsumowanie["zus"] == "100.00"
    assert podsumowanie["podatek_dochodowy"] == "50.00"
    # 1500 - (300 + 50 + 100) = 1050
    assert podsumowanie["zysk_strata"] == "1050.00"
    assert podsumowanie["liczba_faktur_przychodowych"] == 2
    assert podsumowanie["liczba_kosztow"] == 1
    assert podsumowanie["rozbicie_przychodu"] == {"invoices": "1000.00", "margin_invoices": "500.00"}


def test_zbuduj_podsumowanie_pusty_okres_daje_zera():
    podsumowanie = zbuduj_podsumowanie({"invoices": []}, [], [], [])
    assert podsumowanie["zysk_strata"] == "0.00"
