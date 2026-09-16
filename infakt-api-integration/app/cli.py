"""
CLI — pobieranie danych z API v3 inFakt do plików JSON.

    python -m app.cli test-connection
    python -m app.cli fetch --okres 2026-09
    python -m app.cli --env production fetch --okres 2026-09 --output-dir ./data
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .client import InfaktApiError, InfaktClient
from .config import settings
from .pipeline import uruchom_pobieranie

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _zbuduj_klienta(args) -> InfaktClient:
    return InfaktClient(
        api_key=settings.api_key,
        environment=args.env or settings.environment,
        max_retries=settings.max_retries,
        timeout_s=settings.timeout_s,
    )


def cmd_test_connection(args) -> int:
    try:
        client = _zbuduj_klienta(args)
        client.get("invoices.json", params={"page": 1, "limit": 1})
    except InfaktApiError as exc:
        print(f"Połączenie nieudane: {exc}", file=sys.stderr)
        return 1
    _print({"status": "ok", "srodowisko": client.environment})
    return 0


def cmd_fetch(args) -> int:
    try:
        client = _zbuduj_klienta(args)
        podsumowanie = uruchom_pobieranie(
            args.okres,
            client=client,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
    except InfaktApiError as exc:
        print(f"Pobieranie nieudane: {exc}", file=sys.stderr)
        return 1
    _print(podsumowanie)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="infakt", description="Pobieranie danych z inFakt API v3")
    p.add_argument(
        "--env", choices=["sandbox", "production"], default=None, help="nadpisuje INFAKT_ENV"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("test-connection", help="sprawdź klucz API i dostępność środowiska")
    t.set_defaults(func=cmd_test_connection)

    f = sub.add_parser("fetch", help="pobierz dane za okres i zapisz do JSON")
    f.add_argument("--okres", required=True, help="format YYYY-MM, np. 2026-09")
    f.add_argument("--output-dir", default=None, help="nadpisuje INFAKT_OUTPUT_DIR")
    f.set_defaults(func=cmd_fetch)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
