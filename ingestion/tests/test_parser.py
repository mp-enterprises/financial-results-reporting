"""
Testy parsera — najbardziej kruchego elementu systemu.

Uruchomienie:  pytest ingestion/tests -q
"""
from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.parser import (ParseError, normalize, parse_period, parse_workbook,
                        sha256_of, to_decimal)

SAMPLE = Path(__file__).resolve().parents[2] / "sample" / "Rozliczenie_2026M07_ZZMP1.xlsx"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="brak pliku przykładowego")


# --- funkcje pomocnicze -----------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("1234,56", Decimal("1234.56")),
    ("1 234.56", Decimal("1234.56")),
    ("\xa01234.56", Decimal("1234.56")),
    ("12%", Decimal("0.12")),
    (">999", Decimal("999")),
    ("—", None), ("", None), (None, None), ("brak", None),
    (1234.5, Decimal("1234.5")),
])
def test_to_decimal(raw, expected):
    assert to_decimal(raw) == expected


def test_normalize_usuwa_polskie_znaki():
    assert normalize("Zysk partnera (po kosztach)") == "zysk partnera po kosztach"
    assert normalize("KOSZTY OGÓLNE") == "koszty ogolne"
    assert normalize("Wartość magazynu — RAZEM") == "wartosc magazynu razem"


@pytest.mark.parametrize("raw,expected", [
    ("2026  M07", (2026, 7)), ("2026 M7", (2026, 7)),
    ("2025M12", (2025, 12)), ("bez okresu", None), ("2026 M13", None),
])
def test_parse_period(raw, expected):
    assert parse_period(raw) == expected


# --- parsowanie pliku wzorcowego -------------------------------------------
@pytest.fixture(scope="module")
def parsed():
    return parse_workbook(SAMPLE)


def test_naglowek(parsed):
    assert parsed.partner_code == "ZZMP1"
    assert (parsed.period_year, parsed.period_month) == (2026, 7)
    assert parsed.generated_at is not None


def test_kanaly(parsed):
    assert set(parsed.settlements) == {"MAIN", "MJ"}


def test_kwoty_kanalu_glownego(parsed):
    s = parsed.settlements["MAIN"]
    assert s.do_zaplaty == Decimal("60041.44380764045")
    assert s.saldo_towaru == Decimal("126459.39279999997")
    assert s.fv_partnera_szt == 1261
    assert s.kfv_szt == 104
    assert s.stawka_prowizji == Decimal("0.2")
    assert s.magazyn_sku == 1096


def test_arytmetyka_sie_spina(parsed):
    """Najważniejszy test biznesowy: rachunek z pliku musi się zgadzać."""
    for channel, s in parsed.settlements.items():
        assert abs(s.fv_partnera_netto - s.kfv_netto - s.saldo_towaru) < Decimal("0.01"), channel
        assert abs(s.saldo_towaru - s.fv_uslugowa - s.do_zaplaty) < Decimal("0.01"), channel
        expected_fee = max(Decimal(0), s.zysk_po_kosztach * s.stawka_prowizji)
        assert abs(expected_fee - s.prowizja_operatora) < Decimal("0.01"), channel


def test_tabele(parsed):
    assert len(parsed.sales_lines) == 306
    assert len(parsed.stock_lines) == 1096
    assert len({l.index_code for l in parsed.sales_lines}) == 306
    assert len({l.index_code for l in parsed.stock_lines}) == 1096


def test_sumy_z_raportu(parsed):
    suma = sum(l.net_value for l in parsed.sales_lines)
    assert abs(suma - parsed.raport_totals["net_value"]) < Decimal("0.01")
    zysk = sum(l.profit for l in parsed.sales_lines)
    assert abs(zysk - parsed.raport_totals["profit"]) < Decimal("0.01")


def test_stok_zgodny_z_karta(parsed):
    suma = sum(l.purchase_value for l in parsed.stock_lines)
    assert abs(suma - parsed.settlements["MAIN"].magazyn_wartosc) < Decimal("0.01")
    assert sum(l.qty_on_hand for l in parsed.stock_lines) == parsed.settlements["MAIN"].magazyn_szt


def test_pozycje_ujemne_sa_zachowane(parsed):
    """Zwroty przewyższające sprzedaż dają ujemne ilości — nie wolno ich gubić."""
    assert any(l.quantity < 0 for l in parsed.sales_lines)
    assert any(l.profit < 0 for l in parsed.sales_lines)


def test_martwy_stok_ma_pusty_wskaznik(parsed):
    dead = [l for l in parsed.stock_lines if l.stock_status == "martwy stok"]
    assert dead and all(l.days_cover is None for l in dead)


def test_capped_days_cover(parsed):
    """'>999' w pliku musi być oznaczone flagą, a nie zamienione w NULL."""
    capped = [l for l in parsed.stock_lines if l.days_cover_capped]
    assert capped and all(l.days_cover == Decimal("999") for l in capped)


# --- odporność --------------------------------------------------------------
def test_przesuniecie_wierszy_nie_psuje_parsowania(tmp_path):
    """Nadawca wstawia dodatkowy wiersz na górze Karty — parser ma to przetrwać."""
    dst = tmp_path / "przesuniety.xlsx"
    shutil.copy(SAMPLE, dst)
    wb = load_workbook(dst)
    wb["Karta"].insert_rows(1, 3)
    wb["Karta"]["A1"] = "NOWY NAGŁÓWEK DODANY PRZEZ NADAWCĘ"
    wb["Raport"].insert_rows(1, 2)
    wb.save(dst)

    p = parse_workbook(dst)
    assert p.settlements["MAIN"].do_zaplaty == Decimal("60041.44380764045")
    assert len(p.sales_lines) == 306


def test_brak_arkusza_konczy_sie_bledem(tmp_path):
    dst = tmp_path / "bez_stoku.xlsx"
    shutil.copy(SAMPLE, dst)
    wb = load_workbook(dst)
    del wb["Stok"]
    wb.save(dst)
    with pytest.raises(ParseError, match="Brak wymaganych arkuszy"):
        parse_workbook(dst)


def test_pusty_plik_konczy_sie_bledem(tmp_path):
    dst = tmp_path / "pusty.xlsx"
    wb = Workbook()
    wb.active.title = "Arkusz1"
    wb.save(dst)
    with pytest.raises(ParseError):
        parse_workbook(dst)


def test_uszkodzony_plik_konczy_sie_bledem(tmp_path):
    dst = tmp_path / "uszkodzony.xlsx"
    dst.write_bytes(b"to nie jest xlsx")
    with pytest.raises(ParseError, match="Nie można otworzyć"):
        parse_workbook(dst)


def test_partner_z_nazwy_pliku_gdy_brak_w_tresci(tmp_path):
    dst = tmp_path / "Rozliczenie_2025M03_TESTPARTNER.xlsx"
    shutil.copy(SAMPLE, dst)
    wb = load_workbook(dst)
    for sheet in ("Karta", "Jak czytać"):
        for row in wb[sheet].iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.strip().lower().startswith(("partner", "okres")):
                    cell.value = "usunięte"
    wb.save(dst)
    p = parse_workbook(dst)
    assert p.partner_code == "TESTPARTNER"
    assert (p.period_year, p.period_month) == (2025, 3)


def test_sha256_jest_stabilny():
    assert sha256_of(SAMPLE) == sha256_of(SAMPLE)
    assert len(sha256_of(SAMPLE)) == 64
