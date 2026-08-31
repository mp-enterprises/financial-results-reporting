"""
FastAPI — punkt wejścia dla n8n (Cloud lub self-hosted) oraz ręcznego uploadu.

Endpointy maszynowe (nagłówek X-API-Key):
  POST /ingest                  wgranie pliku; mode=sync (domyślnie) lub mode=async
  GET  /files/{sha}             status konkretnego pliku — do odpytywania w trybie async
  GET  /status/okres            czy rozliczenie za dany miesiąc jest już w bazie
  POST /dbt/run                 ręczne przeliczenie modeli

Endpointy administracyjne (Basic Auth):
  GET  /                        formularz ręcznego wgrania
  POST /upload                  obsługa formularza
  GET  /runs, /files, /checks   podgląd stanu

  GET  /healthz                 healthcheck (bez autoryzacji)

Tryb async istnieje, bo n8n Cloud łączy się przez publiczny HTTPS, a Cloudflare
z włączonym proxy przerywa żądanie po 100 sekundach. W trybie async worker
odpowiada natychmiast, a n8n odpytuje o wynik.
"""
from __future__ import annotations

import base64
import binascii
import logging
import secrets
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fastapi import (BackgroundTasks, Depends, FastAPI, File, Form,
                     HTTPException, Request, UploadFile, status)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .config import settings
from .db import connection, run_migrations
from .parser import sha256_of
from .pipeline import ingest_file, run_dbt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="Rozliczenia partnerskie — ingest", version="1.1.0")
basic = HTTPBasic(auto_error=False)

TERMINAL_OK = ("loaded", "transformed")


@app.on_event("startup")
def _startup() -> None:
    repo_migrations = Path(__file__).resolve().parents[2] / "db" / "migrations"
    applied = run_migrations(repo_migrations if repo_migrations.exists() else "/app/db/migrations")
    if applied:
        log.info("Zastosowano migracje: %s", applied)
    settings.archive_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Autoryzacja
# --------------------------------------------------------------------------
def require_api_key(request: Request) -> None:
    if not settings.api_key:
        raise HTTPException(500, "INGEST_API_KEY nie jest ustawiony na serwerze")
    if not secrets.compare_digest(request.headers.get("x-api-key", ""), settings.api_key):
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


# --------------------------------------------------------------------------
# Pomocnicze
# --------------------------------------------------------------------------
def _save_upload(content: bytes, filename: str) -> Path:
    limit = settings.max_upload_mb * 1024 * 1024
    if len(content) > limit:
        raise HTTPException(413, f"Plik przekracza limit {settings.max_upload_mb} MB")
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(415, "Oczekiwano pliku .xlsx")
    tmp = Path(tempfile.mkdtemp()) / Path(filename).name
    tmp.write_bytes(content)
    return tmp


def _lookup_file(sha: str) -> dict | None:
    """Stan pliku po skrócie SHA-256 wraz z wynikiem kontroli kwot."""
    with connection() as conn:
        row = conn.execute(
            """
            SELECT f.file_id, f.file_name, f.status::text, f.partner_code,
                   f.period_year, f.period_month, f.error_message, f.processed_at,
                   (SELECT count(*) FROM ops.data_quality_check c
                     JOIN core.settlement s USING (settlement_id)
                    WHERE s.file_id = f.file_id AND NOT c.passed AND c.severity = 'error')
              FROM raw.ingested_file f
             WHERE f.file_sha256 = %s
            """,
            (sha,),
        ).fetchone()
        if row is None:
            return None
        fid, name, st, partner, y, m, err, processed, failed = row
        wynik = {
            "file_sha256": sha,
            "file_id": fid,
            "file_name": name,
            "status": st,
            "partner_code": partner,
            "period": f"{y}-{m:02d}" if y and m else None,
            "processed_at": processed.isoformat() if processed else None,
            "checks_failed": failed,
            "message": err,
            "zakonczone": st in TERMINAL_OK or st in ("failed", "quarantined"),
            "udane": st in TERMINAL_OK and failed == 0,
        }
        if failed:
            rows = conn.execute(
                """
                SELECT c.check_name, c.expected, c.actual, c.difference
                  FROM ops.data_quality_check c
                  JOIN core.settlement s USING (settlement_id)
                 WHERE s.file_id = %s AND NOT c.passed AND c.severity = 'error'
                """,
                (fid,),
            ).fetchall()
            wynik["failed_checks"] = [
                {"check": n, "expected": float(e or 0), "actual": float(a or 0),
                 "difference": float(d or 0)}
                for n, e, a, d in rows
            ]
        return wynik


def _response_for(result) -> JSONResponse:
    code = {"processed": 200, "duplicate": 200, "failed": 422}[result.status]
    return JSONResponse(status_code=code, content=result.to_dict())


def _przetworz_w_tle(path: str, source: str, reference: str | None,
                     original_name: str, force: bool) -> None:
    try:
        ingest_file(path, source=source, source_reference=reference,
                    original_name=original_name, force=force)
    except Exception:  # noqa: BLE001
        log.exception("Przetwarzanie w tle nie powiodło się dla %s", original_name)


# --------------------------------------------------------------------------
# Endpointy
# --------------------------------------------------------------------------
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source: str = Form("email"),
    source_reference: str | None = Form(None),
    force: bool = Form(False),
    mode: str = Form("sync"),
    _: None = Depends(require_api_key),
):
    """Główny endpoint. mode=async odpowiada natychmiast (kod 202) i przetwarza
    plik w tle — używaj z n8n Cloud, gdzie żądanie idzie przez publiczny HTTPS."""
    if mode not in ("sync", "async"):
        raise HTTPException(400, "mode musi mieć wartość 'sync' albo 'async'")

    name = file.filename or "rozliczenie.xlsx"
    tmp = _save_upload(await file.read(), name)
    sha = sha256_of(tmp)

    # Brama idempotencji sprawdzana od razu — także w trybie async, żeby n8n
    # dostał jednoznaczną odpowiedź "to już było" bez czekania.
    istniejacy = _lookup_file(sha)
    if istniejacy and istniejacy["status"] in TERMINAL_OK and not force:
        return JSONResponse(status_code=200, content={
            "status": "duplicate",
            "file_sha256": sha,
            "file_name": name,
            "file_id": istniejacy["file_id"],
            "period": istniejacy["period"],
            "partner_code": istniejacy["partner_code"],
            "message": "Plik o tym samym skrócie SHA-256 był już przetworzony. Pominięto.",
        })

    if mode == "async":
        background_tasks.add_task(
            _przetworz_w_tle, str(tmp), source, source_reference, name, force
        )
        return JSONResponse(status_code=202, content={
            "status": "accepted",
            "file_sha256": sha,
            "file_name": name,
            "poll_path": f"/files/{sha}",
            "message": "Plik przyjęty do przetwarzania. Odpytuj poll_path o wynik.",
        })

    result = ingest_file(tmp, source=source, source_reference=source_reference,
                         original_name=name, force=force)
    return _response_for(result)


class Base64Payload(BaseModel):
    filename: str = Field(..., examples=["Rozliczenie_2026M07_ZZMP1.xlsx"])
    content_base64: str
    source: str = "email"
    source_reference: str | None = None
    force: bool = False
    mode: str = "sync"


@app.post("/ingest/base64")
def ingest_base64(payload: Base64Payload, background_tasks: BackgroundTasks,
                  _: None = Depends(require_api_key)):
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"Nieprawidłowy base64: {exc}") from exc
    tmp = _save_upload(content, payload.filename)
    sha = sha256_of(tmp)

    istniejacy = _lookup_file(sha)
    if istniejacy and istniejacy["status"] in TERMINAL_OK and not payload.force:
        return JSONResponse(status_code=200, content={
            "status": "duplicate", "file_sha256": sha,
            "file_name": payload.filename, "period": istniejacy["period"],
        })

    if payload.mode == "async":
        background_tasks.add_task(_przetworz_w_tle, str(tmp), payload.source,
                                  payload.source_reference, payload.filename, payload.force)
        return JSONResponse(status_code=202, content={
            "status": "accepted", "file_sha256": sha, "poll_path": f"/files/{sha}",
        })

    result = ingest_file(tmp, source=payload.source, source_reference=payload.source_reference,
                         original_name=payload.filename, force=payload.force)
    return _response_for(result)


@app.get("/files/{sha}")
def file_status(sha: str, _: None = Depends(require_api_key)) -> dict:
    """Stan przetwarzania pliku. n8n odpytuje ten endpoint w trybie async."""
    if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha.lower()):
        raise HTTPException(400, "Nieprawidłowy skrót SHA-256")
    wynik = _lookup_file(sha.lower())
    if wynik is None:
        raise HTTPException(404, "Nie znaleziono pliku o tym skrócie")
    return wynik


@app.get("/status/okres")
def status_okresu(miesiecy_wstecz: int = 1, partner: str | None = None,
                  _: None = Depends(require_api_key)) -> dict:
    """Czy rozliczenie za wskazany miesiąc jest już w bazie.

    Zastępuje węzeł Postgres w n8n — n8n Cloud nie ma dostępu do bazy,
    która stoi w prywatnej sieci Dockera.
    """
    dzis = date.today().replace(day=1)
    for _ in range(max(0, miesiecy_wstecz)):
        dzis = (dzis - timedelta(days=1)).replace(day=1)
    rok, miesiac = dzis.year, dzis.month

    with connection() as conn:
        row = conn.execute(
            """
            SELECT count(*), max(s.do_zaplaty), max(f.file_name)
              FROM core.settlement s
              JOIN core.period p USING (period_id)
              JOIN core.partner pa USING (partner_id)
              LEFT JOIN raw.ingested_file f ON f.file_id = s.file_id
             WHERE s.is_current
               AND p.period_year = %s AND p.period_month = %s
               AND (%s::text IS NULL OR pa.partner_code = %s)
            """,
            (rok, miesiac, partner, partner),
        ).fetchone()
    liczba, kwota, plik = row
    return {
        "oczekiwany_okres": f"{rok}-{miesiac:02d}",
        "juz_jest": liczba > 0,
        "rozliczen": liczba,
        "plik": plik,
        "do_zaplaty": float(kwota) if kwota is not None else None,
    }


@app.post("/dbt/run")
def dbt_run(_: None = Depends(require_api_key)) -> dict:
    dbt_status, dbt_log = run_dbt()
    return {"status": dbt_status, "log_tail": dbt_log[-3000:]}


# --------------------------------------------------------------------------
# Podgląd administracyjny
# --------------------------------------------------------------------------
@app.get("/runs")
def runs(limit: int = 50, _: str = Depends(require_admin)) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT r.run_id, r.trigger, r.started_at, r.finished_at, r.status,
                   r.rows_sales, r.rows_stock, r.settlements, r.dbt_status,
                   f.file_name, f.partner_code, f.period_year, f.period_month,
                   left(r.message, 300)
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
                   period_year, period_month, source, status::text,
                   received_at, processed_at, left(error_message, 300)
              FROM raw.ingested_file ORDER BY received_at DESC LIMIT %s
            """,
            (min(limit, 500),),
        ).fetchall()
    cols = ["file_id", "file_name", "sha_prefix", "partner_code", "period_year",
            "period_month", "source", "status", "received_at", "processed_at", "error"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/checks")
def checks(only_failed: bool = True, limit: int = 100,
           _: str = Depends(require_admin)) -> list[dict]:
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


# --------------------------------------------------------------------------
# Formularz ręcznego wgrania
# --------------------------------------------------------------------------
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
