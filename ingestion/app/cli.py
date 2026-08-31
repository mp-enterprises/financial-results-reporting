"""
CLI — ręczne uruchamianie pipeline'u niezależnie od n8n.

    python -m app.cli migrate
    python -m app.cli ingest ./Rozliczenie_2026M07_ZZMP1.xlsx
    python -m app.cli ingest ./plik.xlsx --force --no-dbt
    python -m app.cli backfill ./archiwum/            # cały katalog, po kolei
    python -m app.cli status
    python -m app.cli dbt
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .db import connection, run_migrations
from .pipeline import ingest_file, run_dbt

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_migrate(args) -> int:
    applied = run_migrations(args.dir)
    _print({"applied": applied or "brak nowych migracji"})
    return 0


def cmd_ingest(args) -> int:
    result = ingest_file(
        args.path, source=args.source, source_reference=args.reference,
        force=args.force, run_dbt_after=not args.no_dbt,
    )
    _print(result.to_dict())
    if result.status == "failed":
        return 1
    return 2 if result.failed_checks else 0


def cmd_backfill(args) -> int:
    files = sorted(Path(args.dir).glob("*.xlsx"))
    if not files:
        print(f"Brak plików .xlsx w {args.dir}", file=sys.stderr)
        return 1
    summary = []
    for i, path in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {path.name}", file=sys.stderr)
        # dbt uruchamiamy raz, na końcu — nie po każdym pliku
        r = ingest_file(path, source="backfill", run_dbt_after=False)
        summary.append({"file": path.name, "status": r.status, "period": r.period,
                        "checks_failed": len(r.failed_checks), "message": r.message})
    dbt_status, _log = run_dbt()
    _print({"files": summary, "dbt": dbt_status})
    return 0 if all(s["status"] != "failed" for s in summary) else 1


def cmd_status(args) -> int:
    with connection() as conn:
        files = conn.execute("""
            SELECT status, count(*) FROM raw.ingested_file GROUP BY status ORDER BY 1
        """).fetchall()
        periods = conn.execute("""
            SELECT p.period_label, s.channel_code, s.revision, s.do_zaplaty
              FROM core.settlement s JOIN core.period p USING (period_id)
             WHERE s.is_current ORDER BY p.period_label DESC, s.channel_code LIMIT 24
        """).fetchall()
        failed = conn.execute("""
            SELECT check_name, expected, actual, difference FROM ops.data_quality_check
             WHERE NOT passed AND severity = 'error' ORDER BY checked_at DESC LIMIT 20
        """).fetchall()
    _print({
        "pliki_wg_statusu": {s: c for s, c in files},
        "aktualne_rozliczenia": [
            {"okres": p, "kanal": c, "rewizja": r, "do_zaplaty": float(d or 0)}
            for p, c, r, d in periods
        ],
        "nieudane_kontrole": [
            {"kontrola": n, "oczekiwano": float(e or 0), "jest": float(a or 0), "roznica": float(d or 0)}
            for n, e, a, d in failed
        ],
    })
    return 0


def cmd_dbt(args) -> int:
    dbt_status, log = run_dbt()
    print(log)
    _print({"status": dbt_status})
    return 0 if dbt_status == "success" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="settlements", description="Pipeline rozliczeń partnerskich")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("migrate", help="zastosuj migracje SQL")
    m.add_argument("--dir", default="/app/db/migrations")
    m.set_defaults(func=cmd_migrate)

    i = sub.add_parser("ingest", help="przetwórz pojedynczy plik")
    i.add_argument("path")
    i.add_argument("--source", default="cli")
    i.add_argument("--reference", default=None)
    i.add_argument("--force", action="store_true",
                   help="przetwórz mimo że plik o tym skrócie już był (tworzy nową rewizję)")
    i.add_argument("--no-dbt", action="store_true", help="pomiń transformacje dbt")
    i.set_defaults(func=cmd_ingest)

    b = sub.add_parser("backfill", help="przetwórz cały katalog plików historycznych")
    b.add_argument("dir")
    b.set_defaults(func=cmd_backfill)

    s = sub.add_parser("status", help="podsumowanie stanu systemu")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("dbt", help="uruchom transformacje dbt")
    d.set_defaults(func=cmd_dbt)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
