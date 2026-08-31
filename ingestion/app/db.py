"""Połączenie z Postgresem (psycopg 3) + uruchamianie migracji."""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from .config import settings

log = logging.getLogger(__name__)
_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings.database_url, min_size=1, max_size=5, open=True)
    return _pool


@contextmanager
def connection():
    """Transakcja: commit przy sukcesie, rollback przy wyjątku."""
    with get_pool().connection() as conn:
        yield conn


def ensure_database(name: str) -> bool:
    """Tworzy bazę pomocniczą (np. metadanych Metabase), jeśli jej nie ma.

    Skrypty z /docker-entrypoint-initdb.d wykonują się WYŁĄCZNIE przy pierwszym
    utworzeniu wolumenu Postgresa. Gdy wolumen już istniał, brakująca baza nigdy
    sama nie powstanie, a Metabase wpada w pętlę restartów z komunikatem
    `database "metabase" does not exist`. Ta funkcja działa przy każdym starcie
    workera, więc naprawia to niezależnie od historii wolumenu.

    Zwraca True, jeśli baza została właśnie utworzona.
    """
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError(f"Niedozwolona nazwa bazy: {name!r}")

    # CREATE DATABASE nie może działać w transakcji — stąd autocommit.
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        istnieje = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if istnieje:
            return False
        wlasciciel = conn.execute("SELECT current_user").fetchone()[0]
        log.warning("Baza %r nie istnieje — tworzę ją", name)
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(wlasciciel)
            )
        )
    return True


def run_migrations(migrations_dir: str | Path = "/app/db/migrations") -> list[str]:
    """Idempotentnie stosuje pliki .sql z katalogu migracji.
    Zastosowane migracje są zapisywane w ops.schema_migration."""
    migrations_dir = Path(migrations_dir)
    applied: list[str] = []
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ops.schema_migration (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        done = {r[0] for r in conn.execute("SELECT filename FROM ops.schema_migration")}
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            log.info("Stosuję migrację %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO ops.schema_migration (filename) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
    return applied
