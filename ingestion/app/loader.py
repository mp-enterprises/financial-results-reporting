"""
Ładowanie sparsowanego pliku do Postgresa.

Kluczowe własności:
  * cała operacja jest w JEDNEJ transakcji — albo wchodzi całość, albo nic;
  * wgranie tego samego okresu ponownie tworzy nową REWIZJĘ i unieważnia
    poprzednią (is_current = FALSE), zamiast nadpisywać dane;
  * po załadowaniu uruchamiane są kontrole rekoncyliacji arytmetyki rozliczenia.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal

from psycopg.types.json import Jsonb

from .parser import ParsedFile, SettlementFacts

log = logging.getLogger(__name__)

SETTLEMENT_COLUMNS = [
    "koszty_ogolne", "koszty_indywidualne", "koszty_platformy", "koszty_transport",
    "zysk_ze_sprzedazy", "zysk_po_kosztach", "stawka_prowizji", "prowizja_operatora",
    "fv_bx", "usluga_razem", "zwroty_wartosciowe", "fv_uslugowa",
    "fv_partnera_netto", "fv_partnera_szt", "kfv_netto", "kfv_szt",
    "saldo_towaru", "srednia_cena_szt", "do_zaplaty",
    "prowizja_marketplace_pct", "prowizja_techniczna_pct", "przelicznik_ceny_fv",
    "magazyn_wartosc", "magazyn_szt", "magazyn_sku",
]


def _upsert_partner(cur, partner_code: str) -> int:
    cur.execute(
        """
        INSERT INTO core.partner (partner_code) VALUES (%s)
        ON CONFLICT (partner_code) DO UPDATE SET partner_code = EXCLUDED.partner_code
        RETURNING partner_id
        """,
        (partner_code,),
    )
    return cur.fetchone()[0]


def _get_period(cur, year: int, month: int) -> int:
    cur.execute("SELECT core.get_or_create_period(%s::smallint, %s::smallint)", (year, month))
    return cur.fetchone()[0]


def _upsert_products(cur, partner_id: int, items: list[tuple[str, str | None]],
                     seen_on: date) -> dict[str, int]:
    """Masowy upsert produktów. Zwraca mapę index_code -> product_id."""
    if not items:
        return {}
    # deduplikacja z zachowaniem pierwszej niepustej nazwy
    dedup: dict[str, str | None] = {}
    for code, name in items:
        if code not in dedup or (dedup[code] is None and name):
            dedup[code] = name

    rows = [(partner_id, code, name, seen_on, seen_on) for code, name in dedup.items()]
    with cur.copy(
        "COPY _tmp_product (partner_id, index_code, product_name, first_seen_at, last_seen_at) FROM STDIN"
    ) as cp:
        for r in rows:
            cp.write_row(r)

    cur.execute("""
        INSERT INTO core.product (partner_id, index_code, product_name, first_seen_at, last_seen_at)
        SELECT partner_id, index_code, product_name, first_seen_at, last_seen_at FROM _tmp_product
        ON CONFLICT (partner_id, index_code) DO UPDATE
           SET product_name  = COALESCE(EXCLUDED.product_name, core.product.product_name),
               first_seen_at = LEAST(core.product.first_seen_at, EXCLUDED.first_seen_at),
               last_seen_at  = GREATEST(core.product.last_seen_at, EXCLUDED.last_seen_at)
    """)
    cur.execute(
        "SELECT index_code, product_id FROM core.product WHERE partner_id = %s AND index_code = ANY(%s)",
        (partner_id, list(dedup.keys())),
    )
    return {code: pid for code, pid in cur.fetchall()}


def _insert_settlement(cur, partner_id: int, period_id: int, file_id: int,
                       facts: SettlementFacts, totals: dict) -> tuple[int, int]:
    """Wstawia rozliczenie jako nową rewizję; unieważnia poprzednią bieżącą."""
    cur.execute(
        """
        SELECT settlement_id, revision FROM core.settlement
         WHERE partner_id = %s AND period_id = %s AND channel_code = %s AND is_current
        """,
        (partner_id, period_id, facts.channel_code),
    )
    prev = cur.fetchone()
    revision = (prev[1] + 1) if prev else 1
    if prev:
        cur.execute(
            "UPDATE core.settlement SET is_current = FALSE WHERE settlement_id = %s", (prev[0],)
        )
        log.warning(
            "Okres %s/%s kanał %s już istniał — tworzę rewizję %s (korekta)",
            period_id, partner_id, facts.channel_code, revision,
        )

    values = [getattr(facts, c) for c in SETTLEMENT_COLUMNS]
    extra_cols = ["raport_total_szt", "raport_total_wartosc", "raport_total_zysk"]
    extra_vals = [totals.get("quantity"), totals.get("net_value"), totals.get("profit")] \
        if facts.channel_code == "MAIN" else [None, None, None]

    cols = ["partner_id", "period_id", "channel_code", "file_id", "revision"] \
        + SETTLEMENT_COLUMNS + extra_cols
    placeholders = ", ".join(["%s"] * len(cols))
    cur.execute(
        f"INSERT INTO core.settlement ({', '.join(cols)}) VALUES ({placeholders}) RETURNING settlement_id",
        [partner_id, period_id, facts.channel_code, file_id, revision] + values + extra_vals,
    )
    settlement_id = cur.fetchone()[0]
    if prev:
        cur.execute(
            "UPDATE core.settlement SET superseded_by = %s WHERE settlement_id = %s",
            (settlement_id, prev[0]),
        )
    return settlement_id, revision


def load_parsed_file(conn, file_id: int, parsed: ParsedFile) -> dict:
    """Ładuje ParsedFile do core.*. Wywoływać wewnątrz transakcji."""
    stats: dict = {"settlements": {}, "revisions": {}}
    period_end = date(parsed.period_year, parsed.period_month, 1)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE _tmp_product (
                partner_id INT, index_code TEXT, product_name TEXT,
                first_seen_at DATE, last_seen_at DATE
            ) ON COMMIT DROP
        """)

        partner_id = _upsert_partner(cur, parsed.partner_code)
        period_id = _get_period(cur, parsed.period_year, parsed.period_month)

        # --- surowe arkusze do raw.sheet_payload (audyt / re-parsing) ---
        for sheet_name, rows in parsed.sheet_payloads.items():
            cur.execute(
                """
                INSERT INTO raw.sheet_payload (file_id, sheet_name, row_count, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (file_id, sheet_name) DO UPDATE
                   SET payload = EXCLUDED.payload, row_count = EXCLUDED.row_count
                """,
                (file_id, sheet_name, len(rows), Jsonb(rows)),
            )

        # --- notatki z "Jak czytać" ---
        for key, value, num in parsed.notes:
            cur.execute(
                """
                INSERT INTO core.settlement_note (file_id, note_key, note_value, note_numeric)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (file_id, note_key) DO UPDATE
                   SET note_value = EXCLUDED.note_value, note_numeric = EXCLUDED.note_numeric
                """,
                (file_id, key, value, num),
            )

        # --- produkty (sprzedaż + magazyn razem) ---
        product_items = [(l.index_code, l.product_name) for l in parsed.sales_lines] \
            + [(l.index_code, l.product_name) for l in parsed.stock_lines]
        product_map = _upsert_products(cur, partner_id, product_items, period_end)

        # --- rozliczenia ---
        for channel, facts in parsed.settlements.items():
            settlement_id, revision = _insert_settlement(
                cur, partner_id, period_id, file_id, facts, parsed.raport_totals
            )
            stats["settlements"][channel] = settlement_id
            stats["revisions"][channel] = revision

        # --- sprzedaż SKU (tylko kanał MAIN) ---
        main_id = stats["settlements"].get("MAIN")
        if main_id:
            cur.execute("DELETE FROM core.sales_line WHERE settlement_id = %s", (main_id,))
            with cur.copy(
                "COPY core.sales_line (settlement_id, product_id, quantity, unit_price, net_value, profit, line_no) FROM STDIN"
            ) as cp:
                for l in parsed.sales_lines:
                    cp.write_row((main_id, product_map[l.index_code], l.quantity,
                                  l.unit_price, l.net_value, l.profit, l.line_no))
            stats["rows_sales"] = len(parsed.sales_lines)

        # --- magazyn (wspólny dla wszystkich kanałów) ---
        # Arkusz Stok jest opcjonalny. Gdy go nie ma, NIE czyścimy istniejącego
        # zdjęcia magazynu dla tego okresu — brak arkusza znaczy "nie wiem",
        # a nie "magazyn jest pusty".
        if parsed.stock_lines:
            cur.execute(
                "DELETE FROM core.stock_snapshot WHERE partner_id = %s AND period_id = %s",
                (partner_id, period_id),
            )
            with cur.copy(
                """COPY core.stock_snapshot (partner_id, period_id, product_id, file_id, qty_on_hand,
                   purchase_value, sales_month, sales_3m, sales_total, avg_daily, days_cover,
                   days_cover_capped, stock_status) FROM STDIN"""
            ) as cp:
                for l in parsed.stock_lines:
                    cp.write_row((partner_id, period_id, product_map[l.index_code], file_id,
                                  l.qty_on_hand, l.purchase_value, l.sales_month, l.sales_3m,
                                  l.sales_total, l.avg_daily, l.days_cover,
                                  l.days_cover_capped, l.stock_status))
        else:
            log.info("Plik %s nie zawiera arkusza Stok — pomijam dane magazynowe", file_id)
        stats["rows_stock"] = len(parsed.stock_lines)

        cur.execute(
            "UPDATE raw.ingested_file SET partner_code = %s, period_year = %s, period_month = %s, generated_at = %s WHERE file_id = %s",
            (parsed.partner_code, parsed.period_year, parsed.period_month,
             parsed.generated_at, file_id),
        )

    stats["partner_id"] = partner_id
    stats["period_id"] = period_id
    return stats


# ---------------------------------------------------------------------------
# Rekoncyliacja — niezależna kontrola arytmetyki rozliczenia.
# Nie ufamy nadawcy: sprawdzamy, czy kwoty w pliku same się spinają.
# ---------------------------------------------------------------------------
def run_reconciliation(conn, run_id: int, settlement_ids: dict[str, int],
                       tolerance: float = 0.01) -> list[dict]:
    results: list[dict] = []
    tol = Decimal(str(tolerance))

    with conn.cursor() as cur:
        for channel, sid in settlement_ids.items():
            cur.execute(
                f"SELECT {', '.join(SETTLEMENT_COLUMNS)}, raport_total_wartosc, raport_total_zysk "
                f"FROM core.settlement WHERE settlement_id = %s",
                (sid,),
            )
            row = cur.fetchone()
            s = dict(zip(SETTLEMENT_COLUMNS + ["raport_total_wartosc", "raport_total_zysk"], row))
            D = lambda k: s[k] if s[k] is not None else Decimal(0)  # noqa: E731

            checks = [
                ("saldo = fv_partnera - kfv",
                 D("fv_partnera_netto") - D("kfv_netto"), D("saldo_towaru"), "error"),
                ("do_zaplaty = saldo - fv_uslugowa",
                 D("saldo_towaru") - D("fv_uslugowa"), D("do_zaplaty"), "error"),
                ("prowizja = max(0; zysk_po_kosztach * stawka)",
                 max(Decimal(0), D("zysk_po_kosztach") * D("stawka_prowizji")),
                 D("prowizja_operatora"), "error"),
                ("zysk_po_kosztach = zysk_ze_sprzedazy - koszty",
                 D("zysk_ze_sprzedazy") - D("koszty_ogolne") - D("koszty_indywidualne"),
                 D("zysk_po_kosztach"), "error"),
                ("usluga = koszty_ogolne + koszty_indyw + prowizja",
                 D("koszty_ogolne") + D("koszty_indywidualne") + D("prowizja_operatora"),
                 D("usluga_razem"), "error"),
                ("fv_uslugowa = usluga + zwroty_wart + fv_bx",
                 D("usluga_razem") + D("zwroty_wartosciowe") + D("fv_bx"),
                 D("fv_uslugowa"), "error"),
            ]
            if channel == "MAIN":
                checks.append((
                    "raport_wartosc ~ saldo_towaru (ostrzegawczo)",
                    D("saldo_towaru"), D("raport_total_wartosc"), "warning",
                ))

            for name, expected, actual, severity in checks:
                diff = (actual or Decimal(0)) - (expected or Decimal(0))
                # dla kontroli ostrzegawczej dopuszczamy 1% odchylenia
                limit = tol if severity == "error" else max(tol, abs(expected) * Decimal("0.01"))
                passed = abs(diff) <= limit
                cur.execute(
                    """
                    INSERT INTO ops.data_quality_check
                        (run_id, settlement_id, check_name, expected, actual, difference, passed, severity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (run_id, sid, f"[{channel}] {name}", expected, actual, diff, passed, severity),
                )
                results.append({
                    "channel": channel, "check": name, "passed": passed,
                    "expected": float(expected), "actual": float(actual),
                    "difference": float(diff), "severity": severity,
                })
    return results
