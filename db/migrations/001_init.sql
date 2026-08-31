-- =============================================================================
-- 001_init.sql — schemat bazy rozliczeń partnerskich
-- Warstwy:
--   raw   — surowe, niezmienne odwzorowanie pliku (audyt, możliwość re-parsingu)
--   core  — znormalizowany model relacyjny (źródło prawdy dla analityki)
--   ops   — metadane procesu (uruchomienia, logi, alerty)
-- Warstwa mart_* jest budowana przez dbt i NIE jest tworzona tutaj.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS mart;      -- zapełniany przez dbt

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- Typy słownikowe
-- -----------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE core.channel_code AS ENUM ('MAIN', 'MJ');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE ops.ingest_status AS ENUM
        ('received', 'parsing', 'loaded', 'transformed', 'failed', 'duplicate', 'quarantined');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- =============================================================================
-- RAW — surowe pliki i ich zawartość
-- =============================================================================

-- Rejestr plików. file_sha256 jest UNIQUE — to główny mechanizm idempotencji:
-- ten sam bajt-w-bajt plik nigdy nie zostanie przetworzony dwa razy.
CREATE TABLE raw.ingested_file (
    file_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_sha256         CHAR(64)      NOT NULL UNIQUE,
    file_name           TEXT          NOT NULL,
    file_size_bytes     BIGINT        NOT NULL,
    storage_path        TEXT          NOT NULL,   -- ścieżka w wolumenie /data/archive
    source              TEXT          NOT NULL,   -- 'email' | 'manual' | 'cli' | 'backfill'
    source_reference    TEXT,                     -- Message-ID maila, login operatora itp.
    received_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    -- zdekodowane z zawartości pliku (z fallbackiem na nazwę pliku)
    partner_code        TEXT,
    period_year         SMALLINT,
    period_month        SMALLINT,
    generated_at        TIMESTAMPTZ,              -- "Wygenerowano" z karty "Jak czytać"
    status              ops.ingest_status NOT NULL DEFAULT 'received',
    error_message       TEXT,
    processed_at        TIMESTAMPTZ,
    CONSTRAINT chk_month CHECK (period_month IS NULL OR period_month BETWEEN 1 AND 12),
    CONSTRAINT chk_year  CHECK (period_year  IS NULL OR period_year BETWEEN 2000 AND 2100)
);
CREATE INDEX idx_ingested_file_period ON raw.ingested_file (partner_code, period_year, period_month);
CREATE INDEX idx_ingested_file_status ON raw.ingested_file (status);

-- Pełny zrzut każdej karty jako JSONB. Pozwala odtworzyć/naprawić parsowanie
-- bez ponownego dostępu do pliku i jest dowodem "co dokładnie przyszło".
CREATE TABLE raw.sheet_payload (
    file_id     BIGINT NOT NULL REFERENCES raw.ingested_file(file_id) ON DELETE CASCADE,
    sheet_name  TEXT   NOT NULL,
    row_count   INT    NOT NULL,
    payload     JSONB  NOT NULL,   -- tablica tablic: wiersze x kolumny
    PRIMARY KEY (file_id, sheet_name)
);

-- =============================================================================
-- CORE — model znormalizowany
-- =============================================================================

CREATE TABLE core.partner (
    partner_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    partner_code TEXT NOT NULL UNIQUE,     -- np. 'ZZMP1'
    partner_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE core.channel (
    channel_code core.channel_code PRIMARY KEY,
    channel_name TEXT NOT NULL,
    payer_name   TEXT NOT NULL,            -- kto realizuje przelew
    description  TEXT
);
INSERT INTO core.channel (channel_code, channel_name, payer_name, description) VALUES
    ('MAIN', 'Kanał główny',      'NIKCORP', 'Rozliczenie główne — arkusz Karta; ma rozbicie na SKU w arkuszu Raport'),
    ('MJ',   'Amazon przez MJ',   'MJ',      'Osobne rozliczenie — arkusz Karta_MJ; brak rozbicia na SKU')
ON CONFLICT DO NOTHING;

-- Kalendarz okresów rozliczeniowych — ułatwia joiny i wykresy szeregów czasowych.
CREATE TABLE core.period (
    period_id     INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_year   SMALLINT NOT NULL,
    period_month  SMALLINT NOT NULL,
    period_start  DATE     NOT NULL,
    period_end    DATE     NOT NULL,
    period_label  TEXT     NOT NULL,        -- '2026-07'
    UNIQUE (period_year, period_month),
    CONSTRAINT chk_period_month CHECK (period_month BETWEEN 1 AND 12)
);

-- -----------------------------------------------------------------------------
-- Rozliczenie (jedna karta = jeden kanał w jednym okresie)
-- Wersjonowanie: naturalny klucz (partner, okres, kanał) + revision.
-- Nowy plik za ten sam okres NIE nadpisuje danych — tworzy kolejną rewizję,
-- a poprzednia dostaje is_current = FALSE. Historia korekt zostaje zachowana.
-- -----------------------------------------------------------------------------
CREATE TABLE core.settlement (
    settlement_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    partner_id      INT               NOT NULL REFERENCES core.partner(partner_id),
    period_id       INT               NOT NULL REFERENCES core.period(period_id),
    channel_code    core.channel_code NOT NULL REFERENCES core.channel(channel_code),
    file_id         BIGINT            NOT NULL REFERENCES raw.ingested_file(file_id),
    revision        INT               NOT NULL DEFAULT 1,
    is_current      BOOLEAN           NOT NULL DEFAULT TRUE,
    superseded_by   BIGINT            REFERENCES core.settlement(settlement_id),

    -- ---- strona usługowa (co partner płaci operatorowi) ----
    koszty_ogolne          NUMERIC(16,4) NOT NULL DEFAULT 0,
    koszty_indywidualne    NUMERIC(16,4) NOT NULL DEFAULT 0,
    koszty_platformy       NUMERIC(16,4),          -- w tym: prowizje marketplace
    koszty_transport       NUMERIC(16,4),          -- w tym: transport
    zysk_ze_sprzedazy      NUMERIC(16,4),          -- zysk po zwrotach, przed kosztami
    zysk_po_kosztach       NUMERIC(16,4),
    stawka_prowizji        NUMERIC(6,4),           -- np. 0.2000
    prowizja_operatora     NUMERIC(16,4),          -- FV NI0 = MAX(0; zysk_po_kosztach * stawka)
    fv_bx                  NUMERIC(16,4) NOT NULL DEFAULT 0,
    usluga_razem           NUMERIC(16,4),          -- koszty ogólne + indyw. + prowizja
    zwroty_wartosciowe     NUMERIC(16,4) NOT NULL DEFAULT 0,
    fv_uslugowa            NUMERIC(16,4),          -- pełna faktura usługowa

    -- ---- strona towarowa (co operator płaci partnerowi) ----
    fv_partnera_netto      NUMERIC(16,4),
    fv_partnera_szt        INTEGER,
    kfv_netto              NUMERIC(16,4) NOT NULL DEFAULT 0,
    kfv_szt                INTEGER       NOT NULL DEFAULT 0,
    saldo_towaru           NUMERIC(16,4),          -- FV - KFV
    srednia_cena_szt       NUMERIC(16,4),
    do_zaplaty             NUMERIC(16,4),          -- saldo - fv_uslugowa

    -- ---- przelicznik ceny (informacyjnie) ----
    prowizja_marketplace_pct NUMERIC(6,4),
    prowizja_techniczna_pct  NUMERIC(6,4),
    przelicznik_ceny_fv      NUMERIC(6,4),

    -- ---- magazyn na koniec okresu (informacyjnie, tylko kanał MAIN) ----
    magazyn_wartosc        NUMERIC(16,4),
    magazyn_szt            INTEGER,
    magazyn_sku            INTEGER,

    -- ---- sumy kontrolne z arkusza Raport (do rekoncyliacji) ----
    raport_total_szt       INTEGER,
    raport_total_wartosc   NUMERIC(16,4),
    raport_total_zysk      NUMERIC(16,4),

    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (partner_id, period_id, channel_code, revision)
);

-- Gwarancja: dokładnie jedna aktualna wersja rozliczenia na (partner, okres, kanał).
CREATE UNIQUE INDEX uq_settlement_current
    ON core.settlement (partner_id, period_id, channel_code)
    WHERE is_current;

CREATE INDEX idx_settlement_file ON core.settlement (file_id);

-- -----------------------------------------------------------------------------
-- Produkty — wymiar wspólny dla sprzedaży i magazynu.
-- Nazwa bywa aktualizowana; trzymamy ostatnią znaną + datę pierwszego widzenia.
-- -----------------------------------------------------------------------------
CREATE TABLE core.product (
    product_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    partner_id    INT  NOT NULL REFERENCES core.partner(partner_id),
    index_code    TEXT NOT NULL,             -- 'DB000003', 'B0F6ML1NL7' ...
    product_name  TEXT,
    is_asin_like  BOOLEAN GENERATED ALWAYS AS (index_code ~ '^B0[A-Z0-9]{8}$') STORED,
    first_seen_at DATE,
    last_seen_at  DATE,
    UNIQUE (partner_id, index_code)
);

-- -----------------------------------------------------------------------------
-- Sprzedaż po korektach w rozbiciu na SKU (arkusz Raport). Tylko kanał MAIN.
-- -----------------------------------------------------------------------------
CREATE TABLE core.sales_line (
    settlement_id BIGINT        NOT NULL REFERENCES core.settlement(settlement_id) ON DELETE CASCADE,
    product_id    BIGINT        NOT NULL REFERENCES core.product(product_id),
    quantity      INTEGER       NOT NULL,       -- może być ujemna (przewaga zwrotów)
    unit_price    NUMERIC(16,4),
    net_value     NUMERIC(16,4) NOT NULL,
    profit        NUMERIC(16,4) NOT NULL,
    line_no       INTEGER,                      -- pozycja w rankingu z pliku
    PRIMARY KEY (settlement_id, product_id)
);
CREATE INDEX idx_sales_line_product ON core.sales_line (product_id);

-- -----------------------------------------------------------------------------
-- Zdjęcie magazynu na koniec okresu (arkusz Stok). Obejmuje WSZYSTKIE kanały,
-- dlatego wiąże się z partnerem i okresem, a nie z konkretnym rozliczeniem.
-- -----------------------------------------------------------------------------
CREATE TABLE core.stock_snapshot (
    partner_id       INT     NOT NULL REFERENCES core.partner(partner_id),
    period_id        INT     NOT NULL REFERENCES core.period(period_id),
    product_id       BIGINT  NOT NULL REFERENCES core.product(product_id),
    file_id          BIGINT  NOT NULL REFERENCES raw.ingested_file(file_id),
    qty_on_hand      INTEGER NOT NULL,
    purchase_value   NUMERIC(16,4) NOT NULL DEFAULT 0,
    sales_month      INTEGER NOT NULL DEFAULT 0,
    sales_3m         INTEGER NOT NULL DEFAULT 0,
    sales_total      INTEGER NOT NULL DEFAULT 0,
    avg_daily        NUMERIC(12,4),
    days_cover       NUMERIC(10,2),          -- NULL gdy '—' (brak sprzedaży)
    days_cover_capped BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE gdy w pliku '>999'
    stock_status     TEXT,                   -- 'OK' | 'martwy stok' | 'wolno rotujący' | 'dozamów' | 'PILNE'
    PRIMARY KEY (partner_id, period_id, product_id)
);
CREATE INDEX idx_stock_status ON core.stock_snapshot (partner_id, period_id, stock_status);

-- -----------------------------------------------------------------------------
-- Nagłówki arkusza "Jak czytać" — zapisane jako pary klucz/wartość.
-- Służą do niezależnej kontroli spójności z wartościami z arkusza Karta.
-- -----------------------------------------------------------------------------
CREATE TABLE core.settlement_note (
    file_id      BIGINT NOT NULL REFERENCES raw.ingested_file(file_id) ON DELETE CASCADE,
    note_key     TEXT   NOT NULL,
    note_value   TEXT,
    note_numeric NUMERIC(18,6),
    PRIMARY KEY (file_id, note_key)
);

-- =============================================================================
-- OPS — obserwowalność procesu
-- =============================================================================
CREATE TABLE ops.pipeline_run (
    run_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id       BIGINT REFERENCES raw.ingested_file(file_id) ON DELETE SET NULL,
    trigger       TEXT NOT NULL,                -- 'email' | 'manual' | 'cli' | 'schedule'
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'running',
    rows_sales    INTEGER,
    rows_stock    INTEGER,
    settlements   INTEGER,
    dbt_status    TEXT,
    message       TEXT
);
CREATE INDEX idx_pipeline_run_started ON ops.pipeline_run (started_at DESC);

-- Wyniki kontroli spójności (rekoncyliacja arytmetyki rozliczenia).
CREATE TABLE ops.data_quality_check (
    check_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        BIGINT REFERENCES ops.pipeline_run(run_id) ON DELETE CASCADE,
    settlement_id BIGINT REFERENCES core.settlement(settlement_id) ON DELETE CASCADE,
    check_name    TEXT NOT NULL,
    expected      NUMERIC(18,6),
    actual        NUMERIC(18,6),
    difference    NUMERIC(18,6),
    passed        BOOLEAN NOT NULL,
    severity      TEXT NOT NULL DEFAULT 'error', -- 'error' | 'warning' | 'info'
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_dq_failed ON ops.data_quality_check (passed, checked_at DESC);

-- -----------------------------------------------------------------------------
-- Funkcja pomocnicza: znajdź lub utwórz okres
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.get_or_create_period(p_year SMALLINT, p_month SMALLINT)
RETURNS INT LANGUAGE plpgsql AS $$
DECLARE v_id INT;
BEGIN
    SELECT period_id INTO v_id FROM core.period
     WHERE period_year = p_year AND period_month = p_month;
    IF v_id IS NULL THEN
        INSERT INTO core.period (period_year, period_month, period_start, period_end, period_label)
        VALUES (
            p_year, p_month,
            make_date(p_year::int, p_month::int, 1),
            (make_date(p_year::int, p_month::int, 1) + INTERVAL '1 month - 1 day')::date,
            to_char(make_date(p_year::int, p_month::int, 1), 'YYYY-MM')
        )
        ON CONFLICT (period_year, period_month) DO NOTHING
        RETURNING period_id INTO v_id;
        IF v_id IS NULL THEN
            SELECT period_id INTO v_id FROM core.period
             WHERE period_year = p_year AND period_month = p_month;
        END IF;
    END IF;
    RETURN v_id;
END $$;

-- -----------------------------------------------------------------------------
-- Rola tylko-do-odczytu dla Metabase (zasada najmniejszych uprawnień)
-- -----------------------------------------------------------------------------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_ro') THEN
        CREATE ROLE metabase_ro LOGIN PASSWORD 'CHANGE_ME_IN_ENV';
    END IF;
END $$;

GRANT USAGE ON SCHEMA core, ops, mart TO metabase_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA core, ops, mart TO metabase_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, ops, mart GRANT SELECT ON TABLES TO metabase_ro;
