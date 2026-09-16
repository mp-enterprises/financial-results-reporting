# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Pipeline that ingests monthly partner-settlement Excel files (`.xlsx`) arriving by
email, validates them, loads them into a layered PostgreSQL model, transforms with
dbt, and exposes the result through Metabase. `README.md` is the authoritative
narrative spec — read it before changing parser, loader, or schema behaviour.

**The codebase is written in Polish** — identifiers, comments, docstrings, DB
columns, dbt model names, log messages. Match that: new code, comments, and commit
messages stay in Polish and follow the existing domain vocabulary (`rozliczenie`,
`kanal`, `okres`, `rewizja`, `saldo`, `do_zaplaty`, `kwarantanna`).

## Commands

Everything Python runs from the `ingestion/` directory (the package is imported as
`app.*`, and tests do `from app.parser import ...`).

```bash
# tests (parser suite; skips gracefully if sample/*.xlsx is absent)
cd ingestion && python -m pytest tests -q
cd ingestion && python -m pytest tests/test_parser.py::test_naglowek -q   # single test

# dbt — needs POSTGRES_HOST/PORT/USER/PASSWORD/DB in the env
dbt build --project-dir dbt --profiles-dir dbt      # models + dbt tests
dbt run   --project-dir dbt --profiles-dir dbt --select mart_partner_pnl

# apply SQL migrations (idempotent; also runs automatically on worker startup)
cd ingestion && python -c "from app.db import run_migrations; print(run_migrations('../db/migrations'))"

# process a file locally
cd ingestion && python -m app.cli ingest ../sample/Rozliczenie_2026M07_ZZMP1.xlsx --no-dbt
cd ingestion && python -m app.cli backfill ../archiwum/     # whole dir, dbt once at end
cd ingestion && python -m app.cli status                    # file counts, current settlements, failed checks
```

`ingest` exit codes: `0` ok, `1` failed/quarantined, `2` loaded but reconciliation
checks failed. `--force` creates a new revision despite an identical file hash.

Docker / deploy helpers live in the `Makefile` (`make up|down|logs|migrate|test|dbt|backup`);
they assume `docker compose` is already running the stack.

CI (`.github/workflows/ci.yml`) runs the parser tests, migrations, loads both
sample files (2026-06 has no `Stok` sheet, 2026-07 has one — both must pass),
asserts the re-load is reported as `duplicate`, then `dbt build`, then asserts
`app.cli status` reports no failed reconciliation checks.

## Architecture

### Ingestion worker (`ingestion/app/`)
FastAPI service; the pipeline is a straight line `file → archive → raw → core →
reconciliation → dbt`.

- **`parser.py`** — most fragile / most critical file. Excel → dataclasses
  (`ParsedFile`, `SettlementFacts`, `SalesLine`, `StockLine`). **Never parse by
  row or column index.** Sheet labels are matched by a normalised form
  (`normalize()` strips Polish diacritics/punctuation); table sheets (`Raport`,
  `Stok`) are read through a column map built from the detected header row
  (`_mapuj_kolumny`). A missing *required* column raises `ParseError` →
  quarantine; a missing *optional* one is a warning. Unknown sheets are archived
  to `raw.sheet_payload` (capped at `MAX_WIERSZY_ZRZUTU` rows) and warned about,
  never fatal. Required sheets: `Karta`, `Raport`. Optional: `Jak czytać`,
  `Karta_MJ`, `Stok` (`Stok` only exists from 2026-M07 onward).
- **`loader.py`** — `ParsedFile` → `core.*` in **one transaction**. Re-loading the
  same `(partner, okres, kanał)` inserts a new `revision`, flips the previous row
  to `is_current = FALSE`, sets `superseded_by`. `run_reconciliation()` then
  independently recomputes 6 settlement equations per channel; a diff above
  `RECON_TOLERANCE` (default 0.01 PLN) is recorded as a failed
  `ops.data_quality_check` (severity `error`) — loading still succeeds.
- **`pipeline.py`** — orchestration + the idempotency gate (see below).
- **`api.py`** — endpoints. Machine endpoints need header `X-API-Key`; admin
  endpoints (`/`, `/upload`, `/runs`, `/files`, `/checks`) use Basic Auth;
  `/healthz` is open. `POST /ingest` with `mode=async` returns `202` immediately
  and processes in a background task — this exists because n8n Cloud reaches the
  worker over public HTTPS and Cloudflare's proxy cuts the request at 100 s. n8n
  then polls `GET /files/{sha}`. `GET /status/okres` replaces a DB node in n8n
  (n8n Cloud cannot reach Postgres, which has no published port).
- **`db.py`** — psycopg3 connection pool. `run_migrations()` applies
  `db/migrations/*.sql` in filename order, tracked in `ops.schema_migration`.
  `ensure_database()` creates the Metabase metadata DB if the Postgres init
  script never ran (existing volume).
- **`config.py`** — all config from env vars, no config files.

### Database (`db/migrations/001_init.sql`)
Four schemas: **`raw`** (immutable JSONB dump of every sheet — lets parsing be
re-done later without re-requesting the file), **`core`** (normalised relational
model, source of truth), **`ops`** (pipeline runs + data-quality checks),
**`mart`** (built by dbt, *not* created here).

Idempotency has three levels: (1) `raw.ingested_file.file_sha256` is `UNIQUE`;
(2) natural key `(partner_id, period_id, channel_code)` with a partial unique
index `WHERE is_current`; (3) line items are DELETE + INSERT inside the load
transaction.

Two channels are **separate rows, separate settlement cards, separate payers**:
`MAIN` (sheet `Karta`, payer NIKCORP, has SKU breakdown in `Raport`) and `MJ`
(sheet `Karta_MJ`, payer MJ / Amazon, no SKU breakdown). `core.stock_snapshot` is
keyed by `(partner, period)` — not by settlement — because stock spans channels.
If a file has no `Stok` sheet the existing snapshot for that period is left
untouched (absence means "unknown", not "empty").

### dbt (`dbt/`)
Project + profile name `rozliczenia`; profile reads `POSTGRES_*` env vars, writes
to schema `mart` (staging views land in `staging`). `dbt build` output/target go
to `/tmp` (see `dbt_project.yml`). Six mart tables: `mart_settlement_monthly`,
`mart_partner_pnl`, `mart_product_performance`, `mart_stock_health`,
`mart_cost_structure`, `mart_pipeline_health`.

**Multi-partner from day one.** Every `mart` table carries `partner_id` /
`partner_code` and every window function partitions by partner, so adding a
partner needs no migration. The Metabase queries in `metabase/sql/` therefore
require a `{{partner}}` variable — without it, two partners' data would mix
silently. Procedure: `docs/onboarding_partnera.html`.

### Deployment
`docker-compose.yml` targets Coolify: containers `postgres`, `ingest`, `metabase`,
`backup`. n8n runs in the cloud, not in the stack. The compose file **deliberately
defines no custom Docker network** — a custom network breaks Coolify/Traefik
routing (404 + self-signed cert). Only `ingest` (`:8000`) and `metabase` (`:3000`)
get domains; Postgres publishes no port. Runbook: `docs/wdrozenie.html`.

## `market_intelligence_api/`
Untracked, unrelated experimental spike against the Allegro API (mostly commented
out). Not part of the settlement pipeline, not wired into anything, not covered by
CI. Leave it alone unless the task is explicitly about it. It contains its own
`.env` with API credentials — never commit or echo those.
