"""Połączenie z Postgresem (psycopg 3) + uruchamianie migracji."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import psycopg
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
