"""
Orkiestracja: plik -> archiwum -> raw -> core -> rekoncyliacja -> dbt.

Idempotencja działa na trzech poziomach:
  1. SHA-256 zawartości pliku (UNIQUE w raw.ingested_file) — identyczny plik
     jest odrzucany natychmiast, bez dotykania core.
  2. Klucz naturalny (partner, okres, kanał) w core.settlement — inny plik za
     ten sam okres tworzy nową rewizję, a poprzednia jest unieważniana.
  3. DELETE + INSERT pozycji w obrębie jednej transakcji — brak duplikatów
     nawet przy ponownym przetwarzaniu.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .db import connection
from .loader import load_parsed_file, run_reconciliation
from .parser import ParseError, parse_workbook, sha256_of

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    status: str                       # 'processed' | 'duplicate' | 'failed'
    file_sha256: str
    file_name: str
    file_id: int | None = None
    run_id: int | None = None
    partner_code: str | None = None
    period: str | None = None
    revisions: dict[str, int] = field(default_factory=dict)
    rows_sales: int = 0
    rows_stock: int = 0
    checks: list[dict] = field(default_factory=list)
    dbt_status: str | None = None
    message: str | None = None

    @property
    def failed_checks(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"] and c["severity"] == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "file_sha256": self.file_sha256,
            "file_name": self.file_name,
            "file_id": self.file_id,
            "run_id": self.run_id,
            "partner_code": self.partner_code,
            "period": self.period,
            "revisions": self.revisions,
            "rows_sales": self.rows_sales,
            "rows_stock": self.rows_stock,
            "checks_total": len(self.checks),
            "checks_failed": len(self.failed_checks),
            "failed_checks": self.failed_checks,
            "dbt_status": self.dbt_status,
            "message": self.message,
        }


def _archive(src: Path, sha: str, original_name: str) -> Path:
    """Kopiuje plik do niezmiennego archiwum pod nazwą zawierającą skrót."""
    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.archive_dir / f"{sha[:16]}__{original_name}"
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest


def run_dbt() -> tuple[str, str]:
    """Uruchamia `dbt build` (modele + testy). Zwraca (status, log)."""
    cmd = [
        "dbt", "build",
        "--project-dir", str(settings.dbt_project_dir),
        "--profiles-dir", str(settings.dbt_profiles_dir),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=settings.dbt_timeout_s
        )
    except FileNotFoundError:
        return "skipped", "dbt nie jest zainstalowany w tym obrazie"
    except subprocess.TimeoutExpired:
        return "timeout", f"dbt przekroczył {settings.dbt_timeout_s}s"
    tail = (proc.stdout or "")[-4000:] + (proc.stderr or "")[-2000:]
    return ("success" if proc.returncode == 0 else "error"), tail


def ingest_file(path: str | Path, *, source: str = "manual",
                source_reference: str | None = None,
                original_name: str | None = None,
                force: bool = False,
                run_dbt_after: bool | None = None) -> IngestResult:
    """Główne wejście pipeline'u. Bezpieczne do wielokrotnego wywołania."""
    path = Path(path)
    file_name = original_name or path.name
    sha = sha256_of(path)
    size = path.stat().st_size
    run_dbt_after = settings.run_dbt_after_load if run_dbt_after is None else run_dbt_after

    result = IngestResult(status="failed", file_sha256=sha, file_name=file_name)

    # --- 1. brama idempotencji ------------------------------------------------
    with connection() as conn:
        row = conn.execute(
            "SELECT file_id, status, partner_code, period_year, period_month "
            "FROM raw.ingested_file WHERE file_sha256 = %s",
            (sha,),
        ).fetchone()
        if row and not force:
            fid, status, pcode, y, m = row
            if status in ("loaded", "transformed"):
                result.status = "duplicate"
                result.file_id = fid
                result.partner_code = pcode
                result.period = f"{y}-{m:02d}" if y and m else None
                result.message = (
                    f"Plik o tym samym skrócie SHA-256 został już przetworzony "
                    f"(file_id={fid}). Pominięto."
                )
                log.info(result.message)
                return result
            # poprzednia próba się nie powiodła — pozwalamy spróbować ponownie
            log.info("Ponawiam plik file_id=%s po statusie %s", fid, status)

    # --- 2. archiwizacja i rejestracja ---------------------------------------
    archived = _archive(path, sha, file_name)

    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.ingested_file
                    (file_sha256, file_name, file_size_bytes, storage_path, source,
                     source_reference, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'parsing')
                ON CONFLICT (file_sha256) DO UPDATE
                    SET status = 'parsing', error_message = NULL,
                        source = EXCLUDED.source, source_reference = EXCLUDED.source_reference
                RETURNING file_id
                """,
                (sha, file_name, size, str(archived), source, source_reference),
            )
            file_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO ops.pipeline_run (file_id, trigger) VALUES (%s, %s) RETURNING run_id",
                (file_id, source),
            )
            run_id = cur.fetchone()[0]
    result.file_id, result.run_id = file_id, run_id

    # --- 3. parsowanie + ładowanie (jedna transakcja) ------------------------
    try:
        parsed = parse_workbook(archived, file_name=file_name)
        result.partner_code = parsed.partner_code
        result.period = f"{parsed.period_year}-{parsed.period_month:02d}"

        with connection() as conn:
            # wcześniejsze pozycje tego pliku usuwamy, by ponowna próba była czysta
            conn.execute("DELETE FROM raw.sheet_payload WHERE file_id = %s", (file_id,))
            stats = load_parsed_file(conn, file_id, parsed)
            checks = run_reconciliation(
                conn, run_id, stats["settlements"], settings.reconciliation_tolerance
            )
            conn.execute(
                "UPDATE raw.ingested_file SET status = 'loaded', processed_at = now(), error_message = NULL WHERE file_id = %s",
                (file_id,),
            )

        result.rows_sales = stats.get("rows_sales", 0)
        result.rows_stock = stats.get("rows_stock", 0)
        result.revisions = stats.get("revisions", {})
        result.checks = checks
        result.status = "processed"

        if result.failed_checks:
            log.error("Rekoncyliacja nie przeszła: %s", result.failed_checks)

    except (ParseError, Exception) as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        log.exception("Ingest nieudany dla %s", file_name)
        status = "quarantined" if isinstance(exc, ParseError) else "failed"
        with connection() as conn:
            conn.execute(
                "UPDATE raw.ingested_file SET status = %s, error_message = %s WHERE file_id = %s",
                (status, msg[:4000], file_id),
            )
            conn.execute(
                "UPDATE ops.pipeline_run SET status = 'failed', finished_at = now(), message = %s WHERE run_id = %s",
                (msg[:4000], run_id),
            )
        result.status = "failed"
        result.message = msg
        return result

    # --- 4. transformacje dbt -------------------------------------------------
    if run_dbt_after:
        dbt_status, dbt_log = run_dbt()
        result.dbt_status = dbt_status
        if dbt_status != "success":
            log.error("dbt zakończył się statusem %s:\n%s", dbt_status, dbt_log)
        with connection() as conn:
            if dbt_status == "success":
                conn.execute(
                    "UPDATE raw.ingested_file SET status = 'transformed' WHERE file_id = %s",
                    (file_id,),
                )
            conn.execute(
                "UPDATE ops.pipeline_run SET dbt_status = %s, message = COALESCE(message,'') || %s WHERE run_id = %s",
                (dbt_status, ("" if dbt_status == "success" else dbt_log[-2000:]), run_id),
            )

    with connection() as conn:
        conn.execute(
            """
            UPDATE ops.pipeline_run
               SET status = %s, finished_at = now(),
                   rows_sales = %s, rows_stock = %s, settlements = %s
             WHERE run_id = %s
            """,
            ("success" if not result.failed_checks else "success_with_warnings",
             result.rows_sales, result.rows_stock, len(result.revisions), run_id),
        )
    return result
