"""
FastAPI — punkt wejścia dla n8n oraz ręcznego uploadu.

Endpointy:
  GET  /healthz            — healthcheck dla Coolify
  POST /ingest             — multipart upload pliku (nagłówek X-API-Key)  [n8n]
  POST /ingest/base64      — wariant dla n8n, gdy wygodniej wysłać JSON z base64
  GET  /runs               — ostatnie uruchomienia pipeline'u (JSON)
  GET  /files              — rejestr plików i ich statusy
  GET  /checks             — nieudane kontrole rekoncyliacji
  POST /dbt/run            — ręczne uruchomienie transformacji
  GET  /                   — prosty formularz ręcznego wgrania (Basic Auth)
"""
from __future__ import annotations

import base64
import binascii
import logging
import secrets
import tempfile
from pathlib import Path

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request,
                     Response, UploadFile, status)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .config import settings
from .db import connection, run_migrations
from .pipeline import ingest_file, run_dbt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="Rozliczenia partnerskie — ingest", version="1.0.0")
basic = HTTPBasic(auto_error=False)


@app.on_event("startup")
def _startup() -> None:
    applied = run_migrations(Path(__file__).resolve().parents[2] / "db" / "migrations"
                             if (Path(__file__).resolve().parents[2] / "db").exists()
                             else "/app/db/migrations")
    if applied:
        log.info("Zastosowano migracje: %s", applied)
    settings.archive_dir.mkdir(parents=True, exist_ok=True)


def require_api_key(request: Request) -> None:
    if not settings.api_key:
        raise HTTPException(500, "INGEST_API_KEY nie jest ustawiony na serwerze")
    provided = request.headers.get("x-api-key", "")
    if not secrets.compare_digest(provided, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nieprawidłowy klucz API")


def require_admin(credentials: HTTPBasicCredentials | None = Depends(basic)) -> str:
    if not settings.admin_password:
        raise HTTPException(500, "ADMIN_PASSWORD nie jest ustawiony na serwerze")
    if credentials is None or not (
        secrets.compare_digest(credentials.username, settings.admin_user)
        and secrets.compare_digest(credentials.password, settings.admin_password)
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Brak autoryzacji",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _save_upload(content: bytes, filename: str) -> Path:
    limit = settings.max_upload_mb * 1024 * 1024
    if len(content) > limit:
        raise HTTPException(413, f"Plik przekracza limit {settings.max_upload_mb} MB")
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(415, "Oczekiwano pliku .xlsx")
    tmp = Path(tempfile.mkdtemp()) / Path(filename).name
    tmp.write_bytes(content)
    return tmp


def _response_for(result) -> JSONResponse:
    code = {"processed": 200, "duplicate": 200, "failed": 422}[result.status]
    if result.status == "processed" and result.failed_checks:
        code = 200  # dane są w bazie, ale sygnalizujemy problem w treści
    return JSONResponse(status_code=code, content=result.to_dict())


@app.get("/healthz")
def healthz() -> dict:
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"baza niedostępna: {exc}") from exc


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    source: str = Form("email"),
    source_reference: str | None = Form(None),
    force: bool = Form(False),
    _: None = Depends(require_api_key),
):
    """Główny endpoint wywoływany przez n8n po odebraniu maila."""
    tmp = _save_upload(await file.read(), file.filename or "rozliczenie.xlsx")
    result = ingest_file(tmp, source=source, source_reference=source_reference,
                         original_name=file.filename, force=force)
    return _response_for(result)


class Base64Payload(BaseModel):
    filename: str = Field(..., examples=["Rozliczenie_2026M07_ZZMP1.xlsx"])
    content_base64: str
    source: str = "email"
    source_reference: str | None = None
    force: bool = False


@app.post("/ingest/base64")
def ingest_base64(payload: Base64Payload, _: None = Depends(require_api_key)):
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"Nieprawidłowy base64: {exc}") from exc
    tmp = _save_upload(content, payload.filename)
    result = ingest_file(tmp, source=payload.source, source_reference=payload.source_reference,
                         original_name=payload.filename, force=payload.force)
    return _response_for(result)


@app.get("/runs")
def runs(limit: int = 50, _: str = Depends(require_admin)) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT r.run_id, r.trigger, r.started_at, r.finished_at, r.status,
                   r.rows_sales, r.rows_stock, r.settlements, r.dbt_status,
                   f.file_name, f.partner_code, f.period_year, f.period_month, left(r.message, 300)
              FROM ops.pipeline_run r
              LEFT JOIN raw.ingested_file f USING (file_id)
             ORDER BY r.started_at DESC LIMIT %s
            """,
            (min(limit, 500),),
        ).fetchall()
    cols = ["run_id", "trigger", "started_at", "finished_at", "status", "rows_sales",
            "rows_stock", "settlements", "dbt_status", "file_name", "partner_code",
            "period_year", "period_month", "message"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/files")
def files(limit: int = 100, _: str = Depends(require_admin)) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT file_id, file_name, left(file_sha256, 12), partner_code,
                   period_year, period_month, source, status, received_at, processed_at,
                   left(error_message, 300)
              FROM raw.ingested_file ORDER BY received_at DESC LIMIT %s
            """,
            (min(limit, 500),),
        ).fetchall()
    cols = ["file_id", "file_name", "sha_prefix", "partner_code", "period_year",
            "period_month", "source", "status", "received_at", "processed_at", "error"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/checks")
def checks(only_failed: bool = True, limit: int = 100, _: str = Depends(require_admin)) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.check_name, c.expected, c.actual, c.difference, c.passed, c.severity,
                   c.checked_at, p.period_label, s.channel_code
              FROM ops.data_quality_check c
              LEFT JOIN core.settlement s USING (settlement_id)
              LEFT JOIN core.period p USING (period_id)
             {"WHERE NOT c.passed" if only_failed else ""}
             ORDER BY c.checked_at DESC LIMIT %s
            """,
            (min(limit, 500),),
        ).fetchall()
    cols = ["check_name", "expected", "actual", "difference", "passed", "severity",
            "checked_at", "period", "channel"]
    return [dict(zip(cols, r)) for r in rows]


@app.post("/dbt/run")
def dbt_run(_: None = Depends(require_api_key)) -> dict:
    dbt_status, dbt_log = run_dbt()
    return {"status": dbt_status, "log_tail": dbt_log[-3000:]}


UPLOAD_PAGE = """<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wgraj rozliczenie</title><style>
:root{color-scheme:light dark}
body{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;max-width:640px;margin:6vh auto;padding:0 20px}
h1{font-size:1.35rem;margin-bottom:.25rem}p.sub{color:#666;margin-top:0}
form{border:1px solid #d5d5dd;border-radius:12px;padding:22px;margin-top:24px}
label{display:block;font-weight:600;margin:14px 0 6px}
input[type=file],input[type=text]{width:100%;padding:9px;border:1px solid #ccc;border-radius:8px;background:transparent;color:inherit}
button{margin-top:20px;padding:11px 20px;border:0;border-radius:8px;background:#2f6df6;color:#fff;font-weight:600;cursor:pointer}
pre{background:#00000010;padding:14px;border-radius:8px;overflow-x:auto;white-space:pre-wrap;margin-top:20px}
</style></head><body>
<h1>Wgraj rozliczenie miesięczne</h1>
<p class="sub">Plik zostanie sprawdzony, zapisany w archiwum i załadowany do bazy.
Ten sam plik wgrany ponownie zostanie pominięty.</p>
<form method="post" action="/upload" enctype="multipart/form-data">
  <label for="f">Plik .xlsx</label>
  <input id="f" type="file" name="file" accept=".xlsx" required>
  <label for="r">Notatka / źródło (opcjonalnie)</label>
  <input id="r" type="text" name="source_reference" placeholder="np. mail od 2026-08-26">
  <button type="submit">Przetwórz plik</button>
</form>
__RESULT__
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def upload_form(_: str = Depends(require_admin)) -> HTMLResponse:
    return HTMLResponse(UPLOAD_PAGE.replace("__RESULT__", ""))


@app.post("/upload", response_class=HTMLResponse)
async def upload_manual(
    file: UploadFile = File(...),
    source_reference: str | None = Form(None),
    _: str = Depends(require_admin),
):
    import html
    import json as _json

    tmp = _save_upload(await file.read(), file.filename or "rozliczenie.xlsx")
    result = ingest_file(tmp, source="manual", source_reference=source_reference,
                         original_name=file.filename)
    badge = {"processed": "✅ Przetworzono", "duplicate": "↺ Plik już był w systemie",
             "failed": "⛔ Błąd"}[result.status]
    if result.status == "processed" and result.failed_checks:
        badge = "⚠️ Załadowano, ale kontrole kwot nie przeszły"
    body = (f"<h2>{badge}</h2><pre>"
            f"{html.escape(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))}"
            f"</pre>")
    return HTMLResponse(UPLOAD_PAGE.replace("__RESULT__", body))
