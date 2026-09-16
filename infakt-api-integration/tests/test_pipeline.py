from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import responses

from app.client import BASE_URLS, InfaktClient
from app.pipeline import uruchom_pobieranie, zakres_miesiaca
from app.resources import WSZYSTKIE_ZASOBY


@pytest.mark.parametrize(
    "okres,oczekiwany",
    [
        ("2026-09", ("2026-09-01", "2026-10-01")),
        ("2026-12", ("2026-12-01", "2027-01-01")),
        ("2026-01", ("2026-01-01", "2026-02-01")),
    ],
)
def test_zakres_miesiaca(okres, oczekiwany):
    assert zakres_miesiaca(okres) == oczekiwany


def test_zakres_miesiaca_nieprawidlowy_format_podnosi_blad():
    with pytest.raises(ValueError):
        zakres_miesiaca("wrzesien-2026")


def test_zakres_miesiaca_nieprawidlowy_miesiac_podnosi_blad():
    with pytest.raises(ValueError):
        zakres_miesiaca("2026-13")


def _zarejestruj_wszystkie_zasoby(pusty_koszt=None, dodatkowy_koszt=None):
    for nazwa, sciezka in WSZYSTKIE_ZASOBY.items():
        if nazwa == "costs":
            rekordy = [dodatkowy_koszt] if dodatkowy_koszt else []
        elif nazwa == "invoices":
            rekordy = [
                {"id": 1, "gross_price": 100000, "invoice_date": "2026-09-15"},
                {"id": 2, "gross_price": 999999, "invoice_date": "2026-01-01"},  # spoza okresu — pomijane
            ]
        else:
            rekordy = []
        responses.add(responses.GET, f"{BASE_URLS['sandbox']}/{sciezka}", json=rekordy, status=200)


@responses.activate
def test_uruchom_pobieranie_zapisuje_pliki_i_podsumowanie(tmp_path: Path, koszt_przykladowy):
    _zarejestruj_wszystkie_zasoby(dodatkowy_koszt=koszt_przykladowy)
    client = InfaktClient(api_key="test-klucz", environment="sandbox", sleep_fn=lambda _: None)

    podsumowanie = uruchom_pobieranie("2026-09", client=client, output_dir=tmp_path)

    assert podsumowanie["okres"] == "2026-09"
    assert podsumowanie["przychod"] == "1000.00"
    assert podsumowanie["koszty"] == "500.00"
    assert podsumowanie["zysk_strata"] == "500.00"

    raw_dir = tmp_path / "raw" / "2026-09"
    assert (raw_dir / "invoices_raw.json").exists()
    assert (raw_dir / "invoices.json").exists()
    assert (raw_dir / "costs_in_period.json").exists()
    # Pliki pośrednie kosztów świadomie nie są już zapisywane.
    assert not (raw_dir / "costs_raw.json").exists()
    assert not (raw_dir / "costs_filtered.json").exists()
    assert not (raw_dir / "costs_excluded_by_denylist.json").exists()

    invoices_w_okresie = json.loads((raw_dir / "invoices.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in invoices_w_okresie] == [1]  # rekord spoza okresu odfiltrowany

    summary_path = tmp_path / "summary" / "2026-09.json"
    assert summary_path.exists()
    zapisane = json.loads(summary_path.read_text(encoding="utf-8"))
    assert zapisane == podsumowanie


@responses.activate
def test_uruchom_pobieranie_stosuje_denylist(tmp_path: Path, koszt_przykladowy, monkeypatch):
    from app.pipeline import settings as pipeline_settings

    monkeypatch.setattr(
        "app.pipeline.settings",
        dataclasses.replace(pipeline_settings, supplier_denylist_nip="1112223344"),
    )
    _zarejestruj_wszystkie_zasoby(dodatkowy_koszt=koszt_przykladowy)
    client = InfaktClient(api_key="test-klucz", environment="sandbox", sleep_fn=lambda _: None)

    podsumowanie = uruchom_pobieranie("2026-09", client=client, output_dir=tmp_path)

    assert podsumowanie["koszty"] == "0.00"
    assert podsumowanie["kosztow_odrzuconych_denylist"] == 1

    koszty_w_pliku = json.loads(
        (tmp_path / "raw" / "2026-09" / "costs_in_period.json").read_text(encoding="utf-8")
    )
    assert koszty_w_pliku == []  # jedyny koszt w okresie odrzucony denylistą
