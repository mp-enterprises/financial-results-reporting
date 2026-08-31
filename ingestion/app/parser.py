"""
Parser rozliczeń partnerskich (.xlsx).

Zasada nadrzędna: NIE parsujemy po numerach wierszy. Etykiety w arkuszach są
dopasowywane po znormalizowanym tekście, a tabele (Raport, Stok) po wykryciu
wiersza nagłówkowego. Dzięki temu wstawienie nowego wiersza w pliku przez
nadawcę nie psuje pipeline'u.

Wszystko, czego parser nie rozumie, kończy się wyjątkiem ParseError — plik
trafia do kwarantanny zamiast wgrać do bazy śmieci.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

# --- oczekiwana struktura pliku --------------------------------------------
SHEET_HOWTO = "Jak czytać"
SHEET_KARTA = "Karta"
SHEET_KARTA_MJ = "Karta_MJ"
SHEET_RAPORT = "Raport"
SHEET_STOK = "Stok"
REQUIRED_SHEETS = {SHEET_KARTA, SHEET_RAPORT, SHEET_STOK}
KNOWN_SHEETS = {SHEET_HOWTO, SHEET_KARTA, SHEET_KARTA_MJ, SHEET_RAPORT, SHEET_STOK}


class ParseError(ValueError):
    """Plik nie odpowiada oczekiwanej strukturze — nie wolno go załadować."""


# --- pomocnicze -------------------------------------------------------------
def normalize(text: Any) -> str:
    """Znormalizowana forma etykiety: bez ogonków, znaków interpunkcyjnych i
    wielokrotnych spacji. 'FV NI0 (prowizja = MAX(0; ...))' -> 'fv ni0 prowizja max 0'."""
    if text is None:
        return ""
    s = str(text)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ł", "l").replace("Ł", "L")
    s = s.lower()
    s = re.sub(r"[^a-z0-9%]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def to_decimal(value: Any) -> Decimal | None:
    """Liczba z komórki -> Decimal. Obsługuje '—', '>999', '1 234,56', '12%'."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = str(value).strip()
    if s in {"", "-", "—", "–", "n/a", "N/A", "brak"}:
        return None
    capped = s.startswith(">")
    s = s.lstrip(">").lstrip("<").strip()
    s = s.replace("\xa0", "").replace(" ", "")
    pct = s.endswith("%")
    s = s.rstrip("%").replace(",", ".")
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if pct:
        d = d / Decimal(100)
    return d


def to_int(value: Any) -> int | None:
    d = to_decimal(value)
    return int(d) if d is not None else None


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- struktury danych -------------------------------------------------------
@dataclass
class SettlementFacts:
    """Odwzorowanie jednej karty rozliczeniowej (kanał MAIN albo MJ)."""
    channel_code: str
    koszty_ogolne: Decimal | None = None
    koszty_indywidualne: Decimal | None = None
    koszty_platformy: Decimal | None = None
    koszty_transport: Decimal | None = None
    zysk_ze_sprzedazy: Decimal | None = None
    zysk_po_kosztach: Decimal | None = None
    stawka_prowizji: Decimal | None = None
    prowizja_operatora: Decimal | None = None
    fv_bx: Decimal = Decimal(0)
    usluga_razem: Decimal | None = None
    zwroty_wartosciowe: Decimal = Decimal(0)
    fv_uslugowa: Decimal | None = None
    fv_partnera_netto: Decimal | None = None
    fv_partnera_szt: int | None = None
    kfv_netto: Decimal = Decimal(0)
    kfv_szt: int = 0
    saldo_towaru: Decimal | None = None
    srednia_cena_szt: Decimal | None = None
    do_zaplaty: Decimal | None = None
    prowizja_marketplace_pct: Decimal | None = None
    prowizja_techniczna_pct: Decimal | None = None
    przelicznik_ceny_fv: Decimal | None = None
    magazyn_wartosc: Decimal | None = None
    magazyn_szt: int | None = None
    magazyn_sku: int | None = None


@dataclass
class SalesLine:
    index_code: str
    product_name: str | None
    quantity: int
    unit_price: Decimal | None
    net_value: Decimal
    profit: Decimal
    line_no: int


@dataclass
class StockLine:
    index_code: str
    product_name: str | None
    qty_on_hand: int
    purchase_value: Decimal
    sales_month: int
    sales_3m: int
    sales_total: int
    avg_daily: Decimal | None
    days_cover: Decimal | None
    days_cover_capped: bool
    stock_status: str | None


@dataclass
class ParsedFile:
    partner_code: str
    period_year: int
    period_month: int
    generated_at: datetime | None
    settlements: dict[str, SettlementFacts]          # 'MAIN' / 'MJ'
    sales_lines: list[SalesLine]
    stock_lines: list[StockLine]
    raport_totals: dict[str, Any]
    notes: list[tuple[str, str | None, Decimal | None]]
    sheet_payloads: dict[str, list[list[Any]]] = field(default_factory=dict)
    unknown_sheets: list[str] = field(default_factory=list)


# --- parsowanie okresu i partnera -------------------------------------------
PERIOD_RE = re.compile(r"(20\d{2})\s*[-_ ]?\s*M\s*(\d{1,2})", re.IGNORECASE)
FILENAME_RE = re.compile(r"(20\d{2})M(\d{2})_([A-Za-z0-9]+)", re.IGNORECASE)


def parse_period(text: Any) -> tuple[int, int] | None:
    if text is None:
        return None
    m = PERIOD_RE.search(str(text))
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return year, month


def _rows(ws) -> list[list[Any]]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _find_label(rows: Iterable[list[Any]], *candidates: str,
                value_col: int = 1, exact: bool = False) -> Any:
    """Zwraca wartość z kolumny value_col z pierwszego wiersza, którego
    etykieta (kolumna 0) pasuje do któregoś z kandydatów."""
    wanted = [normalize(c) for c in candidates]
    for row in rows:
        if not row:
            continue
        label = normalize(row[0])
        if not label:
            continue
        for w in wanted:
            if (label == w) if exact else (label.startswith(w) or w in label):
                if len(row) > value_col:
                    return row[value_col]
                return None
    return None


def _require(value: Any, label: str) -> Decimal:
    d = to_decimal(value)
    if d is None:
        raise ParseError(f"Brak wymaganej wartości liczbowej: {label!r}")
    return d


# --- karty rozliczeniowe ----------------------------------------------------
def parse_karta(rows: list[list[Any]]) -> SettlementFacts:
    f = SettlementFacts(channel_code="MAIN")
    g = lambda *lbl: _find_label(rows, *lbl)  # noqa: E731

    f.koszty_ogolne = to_decimal(g("koszty ogolne")) or Decimal(0)
    f.koszty_indywidualne = to_decimal(g("koszty indywidualne")) or Decimal(0)
    f.koszty_platformy = to_decimal(g("w tym platformy", "w tym prowizje platform"))
    f.koszty_transport = to_decimal(g("w tym transport"))
    f.zysk_ze_sprzedazy = to_decimal(g("zysk partnera ze sprzedazy"))
    f.zysk_po_kosztach = to_decimal(g("zysk partnera po kosztach"))
    f.stawka_prowizji = to_decimal(g("stawka prowizji nikcorp", "stawka prowizji"))
    f.prowizja_operatora = to_decimal(g("fv ni0"))
    f.fv_bx = to_decimal(g("fv z bx")) or Decimal(0)
    f.usluga_razem = to_decimal(g("usluga koszty ogolne"))
    f.zwroty_wartosciowe = to_decimal(g("zwroty wartosciowe")) or Decimal(0)
    f.fv_uslugowa = _require(g("fv uslugowa"), "FV usługowa")

    f.fv_partnera_netto = _require(g("fv partnera"), "FV partnera")
    f.kfv_netto = to_decimal(g("kfv partnera")) or Decimal(0)
    f.saldo_towaru = _require(g("saldo fv i kfv", "saldo fv kfv", "saldo"), "Saldo")
    f.srednia_cena_szt = to_decimal(g("srednia cena za szt"))
    f.do_zaplaty = _require(g("do zaplaty"), "Do zapłaty")

    f.prowizja_marketplace_pct = to_decimal(g("prowizja marketplace", "prowizja mp"))
    f.prowizja_techniczna_pct = to_decimal(g("prowizja techniczna"))
    f.przelicznik_ceny_fv = to_decimal(g("cena na fv cena sprzedazy"))

    f.magazyn_wartosc = to_decimal(g("wartosc magazynu"))
    f.magazyn_sku = to_int(g("pozycji sku na stanie"))

    # ilości sztuk siedzą w trzeciej kolumnie tych samych wierszy
    f.fv_partnera_szt = to_int(_find_label(rows, "fv partnera", value_col=2))
    f.kfv_szt = to_int(_find_label(rows, "kfv partnera", value_col=2)) or 0
    f.magazyn_szt = to_int(_find_label(rows, "wartosc magazynu", value_col=2))
    return f


def parse_karta_mj(rows: list[list[Any]]) -> SettlementFacts:
    f = SettlementFacts(channel_code="MJ")
    g = lambda *lbl: _find_label(rows, *lbl)  # noqa: E731

    f.koszty_ogolne = to_decimal(g("koszty ogolne")) or Decimal(0)
    f.koszty_indywidualne = to_decimal(g("koszty przydzielone", "razem koszty")) or Decimal(0)
    f.koszty_transport = to_decimal(g("koszty przydzielone transport", "koszty przydzielone"))
    f.zysk_ze_sprzedazy = to_decimal(g("zysk partnera ze sprzedazy"))
    f.zysk_po_kosztach = to_decimal(g("zysk po kosztach"))
    f.stawka_prowizji = to_decimal(g("stawka prowizji"))
    f.prowizja_operatora = to_decimal(g("prowizja 20", "prowizja"))
    f.usluga_razem = to_decimal(g("usluga mj"))
    f.fv_uslugowa = f.usluga_razem            # w kanale MJ usługa == cała faktura

    f.fv_partnera_netto = _require(g("fv partnera"), "FV partnera (MJ)")
    f.kfv_netto = to_decimal(g("kfv partnera")) or Decimal(0)
    f.saldo_towaru = _require(g("saldo"), "Saldo (MJ)")
    f.srednia_cena_szt = to_decimal(g("srednia cena za szt"))
    f.do_zaplaty = _require(g("do zaplaty"), "Do zapłaty (MJ)")

    f.prowizja_marketplace_pct = to_decimal(g("prowizja mp", "prowizja marketplace"))
    f.prowizja_techniczna_pct = to_decimal(g("prowizja techniczna"))
    f.przelicznik_ceny_fv = to_decimal(g("cena na fv cena sprzedazy"))

    f.fv_partnera_szt = to_int(_find_label(rows, "fv partnera", value_col=2))
    f.kfv_szt = to_int(_find_label(rows, "kfv partnera", value_col=2)) or 0
    return f


# --- tabele -----------------------------------------------------------------
def _find_header_row(rows: list[list[Any]], first_cell: str, min_cols: int) -> int:
    target = normalize(first_cell)
    for i, row in enumerate(rows):
        if row and normalize(row[0]) == target and sum(c is not None for c in row) >= min_cols:
            return i
    raise ParseError(f"Nie znaleziono wiersza nagłówkowego zaczynającego się od {first_cell!r}")


def parse_raport(rows: list[list[Any]]) -> tuple[list[SalesLine], dict[str, Any]]:
    header_idx = _find_header_row(rows, "Indeks", 5)

    totals: dict[str, Any] = {}
    for row in rows[:header_idx]:
        if row and normalize(row[0]) == "razem":
            totals = {
                "quantity": to_int(row[2]) if len(row) > 2 else None,
                "net_value": to_decimal(row[4]) if len(row) > 4 else None,
                "profit": to_decimal(row[5]) if len(row) > 5 else None,
            }
            break

    lines: list[SalesLine] = []
    for n, row in enumerate(rows[header_idx + 1:], start=1):
        if not row or row[0] is None or str(row[0]).strip() == "":
            continue
        code = str(row[0]).strip()
        if normalize(code) in {"razem", "suma", "total"}:
            continue
        net = to_decimal(row[4]) if len(row) > 4 else None
        profit = to_decimal(row[5]) if len(row) > 5 else None
        if net is None and profit is None:
            continue
        lines.append(SalesLine(
            index_code=code,
            product_name=str(row[1]).strip() if len(row) > 1 and row[1] is not None else None,
            quantity=to_int(row[2]) or 0,
            unit_price=to_decimal(row[3]) if len(row) > 3 else None,
            net_value=net if net is not None else Decimal(0),
            profit=profit if profit is not None else Decimal(0),
            line_no=n,
        ))
    if not lines:
        raise ParseError("Arkusz Raport nie zawiera żadnych pozycji sprzedaży")
    return lines, totals


def parse_stok(rows: list[list[Any]]) -> list[StockLine]:
    header_idx = _find_header_row(rows, "Indeks", 8)
    lines: list[StockLine] = []
    for row in rows[header_idx + 1:]:
        if not row or row[0] is None or str(row[0]).strip() == "":
            continue
        code = str(row[0]).strip()
        if normalize(code) in {"razem", "suma", "total"}:
            continue
        raw_cover = row[8] if len(row) > 8 else None
        capped = isinstance(raw_cover, str) and raw_cover.strip().startswith(">")
        lines.append(StockLine(
            index_code=code,
            product_name=str(row[1]).strip() if len(row) > 1 and row[1] is not None else None,
            qty_on_hand=to_int(row[2]) or 0,
            purchase_value=to_decimal(row[3]) or Decimal(0),
            sales_month=to_int(row[4]) or 0,
            sales_3m=to_int(row[5]) or 0,
            sales_total=to_int(row[6]) or 0,
            avg_daily=to_decimal(row[7]) if len(row) > 7 else None,
            days_cover=to_decimal(raw_cover),
            days_cover_capped=capped,
            stock_status=str(row[9]).strip() if len(row) > 9 and row[9] is not None else None,
        ))
    if not lines:
        raise ParseError("Arkusz Stok nie zawiera żadnych pozycji")
    return lines


def parse_notes(rows: list[list[Any]]) -> tuple[list[tuple[str, str | None, Decimal | None]], datetime | None]:
    notes: list[tuple[str, str | None, Decimal | None]] = []
    generated_at: datetime | None = None
    seen: set[str] = set()
    for row in rows:
        if not row or row[0] is None:
            continue
        label = str(row[0]).strip()
        key = normalize(label)
        if not key or key in seen:
            continue
        value = row[1] if len(row) > 1 else None
        num = to_decimal(value)
        if key.startswith("wygenerowano"):
            raw = value if value is not None else label.split(":", 1)[-1]
            if isinstance(raw, datetime):
                generated_at = raw
            else:
                try:
                    generated_at = datetime.strptime(str(raw).strip(), "%Y-%m-%d %H:%M")
                except ValueError:
                    generated_at = None
        if value is None and num is None:
            continue
        seen.add(key)
        notes.append((key[:200], None if value is None else str(value)[:500], num))
    return notes, generated_at


# --- wejście publiczne ------------------------------------------------------
def parse_workbook(path: str | Path, file_name: str | None = None) -> ParsedFile:
    path = Path(path)
    file_name = file_name or path.name
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Nie można otworzyć pliku jako XLSX: {exc}") from exc

    try:
        present = set(wb.sheetnames)
        missing = REQUIRED_SHEETS - present
        if missing:
            raise ParseError(
                f"Brak wymaganych arkuszy: {sorted(missing)}. Znalezione: {sorted(present)}"
            )

        sheets = {name: _rows(wb[name]) for name in wb.sheetnames}
    finally:
        wb.close()

    karta_rows = sheets[SHEET_KARTA]

    # --- partner i okres: karta -> "Jak czytać" -> nazwa pliku ---
    partner = _find_label(karta_rows, "partner")
    period_raw = _find_label(karta_rows, "okres")
    if SHEET_HOWTO in sheets:
        partner = partner or _find_label(sheets[SHEET_HOWTO], "partner")
        period_raw = period_raw or _find_label(sheets[SHEET_HOWTO], "okres")
    period = parse_period(period_raw)

    if partner is None or period is None:
        m = FILENAME_RE.search(file_name)
        if not m:
            raise ParseError(
                f"Nie udało się ustalić partnera/okresu ani z zawartości, ani z nazwy pliku {file_name!r}"
            )
        period = period or (int(m.group(1)), int(m.group(2)))
        partner = partner or m.group(3)

    partner_code = str(partner).strip().upper()
    year, month = period

    # --- karty ---
    settlements = {"MAIN": parse_karta(karta_rows)}
    if SHEET_KARTA_MJ in sheets:
        mj_rows = sheets[SHEET_KARTA_MJ]
        if to_decimal(_find_label(mj_rows, "fv partnera")) is not None:
            settlements["MJ"] = parse_karta_mj(mj_rows)

    sales_lines, raport_totals = parse_raport(sheets[SHEET_RAPORT])
    stock_lines = parse_stok(sheets[SHEET_STOK])
    notes, generated_at = parse_notes(sheets.get(SHEET_HOWTO, []))

    return ParsedFile(
        partner_code=partner_code,
        period_year=year,
        period_month=month,
        generated_at=generated_at,
        settlements=settlements,
        sales_lines=sales_lines,
        stock_lines=stock_lines,
        raport_totals=raport_totals,
        notes=notes,
        sheet_payloads={
            name: [[_jsonable(c) for c in row] for row in rows]
            for name, rows in sheets.items()
        },
        unknown_sheets=sorted(set(sheets) - KNOWN_SHEETS),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
