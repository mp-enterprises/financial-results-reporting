from __future__ import annotations

import pytest
import responses

from app.client import BASE_URLS, InfaktClient
from app.resources import (
    POLA_DATY,
    WSZYSTKIE_ZASOBY,
    filtruj_po_okresie,
    filtruj_zrodlo_ksef,
    pobierz_zasob,
    w_okresie,
    zbuduj_filtr_daty,
)


def test_zbuduj_filtr_daty_domyslne_pole():
    assert zbuduj_filtr_daty("2026-09-01", "2026-10-01") == {
        "invoice_date_gteq": "2026-09-01",
        "invoice_date_lt": "2026-10-01",
    }


def test_zbuduj_filtr_daty_niestandardowe_pole():
    filtr = zbuduj_filtr_daty("2026-09-01", "2026-10-01", pole="payment_date")
    assert filtr == {"payment_date_gteq": "2026-09-01", "payment_date_lt": "2026-10-01"}


def test_pobierz_zasob_nieznana_nazwa_podnosi_blad(client: InfaktClient):
    with pytest.raises(ValueError):
        pobierz_zasob(client, "nieistniejacy_zasob")


@pytest.mark.parametrize("nazwa,sciezka", sorted(WSZYSTKIE_ZASOBY.items()))
@responses.activate
def test_pobierz_zasob_woła_wlasciwa_sciezke(client: InfaktClient, nazwa, sciezka):
    responses.add(responses.GET, f"{BASE_URLS['sandbox']}/{sciezka}", json=[{"id": 1}], status=200)
    wynik = pobierz_zasob(client, nazwa)
    assert wynik == [{"id": 1}]


def test_w_okresie_dopasowuje_zakres():
    assert w_okresie({"invoice_date": "2026-06-15"}, "invoice_date", "2026-06-01", "2026-07-01")
    assert not w_okresie({"invoice_date": "2026-07-01"}, "invoice_date", "2026-06-01", "2026-07-01")
    assert not w_okresie({"invoice_date": "2026-05-31"}, "invoice_date", "2026-06-01", "2026-07-01")


def test_w_okresie_akceptuje_znaczniki_czasu_iso():
    assert w_okresie(
        {"invoice_date": "2026-06-15T10:30:00+02:00"}, "invoice_date", "2026-06-01", "2026-07-01"
    )


def test_w_okresie_brak_pola_zwraca_false():
    assert not w_okresie({}, "invoice_date", "2026-06-01", "2026-07-01")


def test_filtruj_po_okresie_rozdziela_w_zakresie_i_bez_daty():
    rekordy = [
        {"invoice_date": "2026-06-15"},
        {"invoice_date": "2026-09-01"},
        {"inny_klucz": True},
    ]
    w_zakresie, bez_daty = filtruj_po_okresie(rekordy, "invoice_date", "2026-06-01", "2026-07-01")
    assert w_zakresie == [{"invoice_date": "2026-06-15"}]
    assert bez_daty == 1


def test_pola_daty_pokrywaja_wszystkie_faktury_i_koszty():
    from app.resources import FAKTURY_PRZYCHODOWE, KOSZTY

    for nazwa in {**FAKTURY_PRZYCHODOWE, **KOSZTY}:
        assert nazwa in POLA_DATY


def test_filtruj_zrodlo_ksef_przepuszcza_tylko_ksef():
    koszty = [
        {"id": 1, "source": "ksef"},
        {"id": 2, "source": "infakt"},
        {"id": 3},
    ]
    assert filtruj_zrodlo_ksef(koszty) == [{"id": 1, "source": "ksef"}]


def test_filtruj_ksef_faktury_przepuszcza_tylko_z_numerem_ksef():
    from app.resources import filtruj_ksef_faktury

    faktury = [
        {"id": 1, "ksef_number": "ABC-123"},
        {"id": 2, "ksef_number": None},
        {"id": 3, "ksef_number": ""},
        {"id": 4},
    ]
    assert filtruj_ksef_faktury(faktury) == [{"id": 1, "ksef_number": "ABC-123"}]
