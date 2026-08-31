# System rozliczeń partnerskich

Automatyczne przetwarzanie miesięcznych rozliczeń przychodzących mailem w formie
pliku Excel: odbiór → walidacja → baza relacyjna → transformacje → dashboardy.

```
Mail z załącznikiem .xlsx
        │
        ▼
   ┌─────────┐   webhook HTTP (X-API-Key)   ┌──────────────────────────┐
   │   n8n   │ ───────────────────────────► │  worker "ingest"         │
   │  IMAP   │                              │  parser → loader → dbt   │
   └─────────┘                              └────────────┬─────────────┘
        ▲                                                │
   formularz                                             ▼
   ręcznego                                    ┌──────────────────┐
   uploadu ─────────────────────────────────►  │   PostgreSQL     │
                                               │ raw / core / mart│
                                               └────────┬─────────┘
                                                        ▼
                                                  ┌──────────┐
                                                  │ Metabase │
                                                  └──────────┘
```

## Dlaczego tak

**Trzy warstwy w bazie.** `raw` przechowuje niezmienny zrzut każdego arkusza w
JSONB — jeśli za pół roku okaże się, że parser źle interpretował jakąś kolumnę,
dane da się przeliczyć bez proszenia nadawcy o ponowne przesłanie plików.
`core` to znormalizowany model relacyjny. `mart` to gotowe tabele analityczne
budowane przez dbt.

**Idempotencja na trzech poziomach.** Skrót SHA-256 zawartości pliku jest
`UNIQUE` — identyczny plik nigdy nie zostanie przetworzony dwa razy. Klucz
naturalny `(partner, okres, kanał)` z indeksem częściowym `WHERE is_current`
gwarantuje jedno aktualne rozliczenie na okres. Ładowanie pozycji odbywa się
w schemacie DELETE + INSERT w jednej transakcji.

**Korekty nie nadpisują historii.** Jeśli nadawca przyśle poprawioną wersję za
ten sam miesiąc, powstaje rewizja nr 2, a poprzednia dostaje `is_current = FALSE`
i wskazanie `superseded_by`. Widoki analityczne pokazują tylko wersję aktualną,
ale audyt jest zachowany.

**Nie ufamy arytmetyce w pliku.** Po każdym załadowaniu system niezależnie
przelicza sześć równań rozliczenia (saldo, kwota do zapłaty, prowizja, zysk po
kosztach, usługa, faktura usługowa). Rozbieżność powyżej grosza to alert, nie
cichy błąd w raporcie.

**Parsowanie po etykietach, nie po numerach wierszy.** Wstawienie przez nadawcę
dodatkowego wiersza w arkuszu nie psuje procesu — potwierdzone testem.

## Struktura repozytorium

```
db/migrations/001_init.sql      schemat raw / core / ops + role tylko-do-odczytu
db/init/                        tworzenie baz pomocniczych (Metabase, n8n)
ingestion/app/
  parser.py                     Excel → struktury danych (najbardziej krytyczny plik)
  loader.py                     struktury → Postgres + rekoncyliacja
  pipeline.py                   orkiestracja i idempotencja
  api.py                        FastAPI: webhook dla n8n + formularz ręczny
  cli.py                        uruchamianie z linii poleceń
ingestion/tests/                testy parsera (13 przypadków)
dbt/models/staging/             widoki normalizujące
dbt/models/marts/               6 tabel analitycznych
n8n/workflow_rozliczenia.json   gotowy workflow do zaimportowania
metabase/sql/                   29 zweryfikowanych zapytań pod karty dashboardów
docker-compose.yml              stack dla Coolify
```

## Model danych

| Tabela | Ziarno | Zawiera |
|---|---|---|
| `core.settlement` | partner × okres × kanał × rewizja | całą kartę rozliczeniową: koszty, prowizję, saldo, kwotę do zapłaty |
| `core.sales_line` | rozliczenie × produkt | sprzedaż po korektach (arkusz Raport, kanał główny) |
| `core.stock_snapshot` | partner × okres × produkt | stan magazynu na koniec okresu (wszystkie kanały) |
| `core.product` | produkt | wymiar wspólny dla sprzedaży i magazynu |
| `core.settlement_note` | plik × klucz | wartości z arkusza „Jak czytać” do kontroli krzyżowej |

Dwa kanały (`MAIN` — NIKCORP, `MJ` — Amazon przez MJ) są osobnymi wierszami,
bo to dwa oddzielne przelewy od dwóch różnych podmiotów. Model `mart_partner_pnl`
konsoliduje je z zachowaniem rozbicia.

### Tabele analityczne

| Model | Do czego służy |
|---|---|
| `mart_settlement_monthly` | rachunek per kanał, dynamika MoM, kolumny kontrolne |
| `mart_partner_pnl` | skonsolidowany wynik, rentowność, rotacja kapitału |
| `mart_product_performance` | rentowność SKU, klasyfikacja ABC, wzrosty i spadki |
| `mart_stock_health` | kapitał zamrożony, dni pokrycia, rekomendacje zakupowe |
| `mart_cost_structure` | struktura kosztów w formacie długim (pod wykresy) |
| `mart_pipeline_health` | stan procesu: co dotarło, co się nie udało |

## Uruchomienie lokalne

```bash
cp .env.example .env          # uzupełnij hasła
docker compose up -d postgres
docker compose up -d --build ingest

# migracje wykonują się same przy starcie workera; ręcznie:
docker compose exec ingest python -m app.cli migrate

# wgranie pliku
docker compose exec ingest python -m app.cli ingest /data/archive/plik.xlsx

# stan systemu
docker compose exec ingest python -m app.cli status
```

## Ręczne uruchamianie procesu

Trzy niezależne drogi — żadna nie wymaga n8n:

1. **Formularz w przeglądarce** — `https://rozliczenia.twojadomena.pl/`
   (Basic Auth), wybierz plik i kliknij „Przetwórz plik”.
2. **CLI** — `python -m app.cli ingest ścieżka/do/pliku.xlsx`
   (`--force` tworzy nową rewizję mimo tego samego skrótu pliku).
3. **HTTP** —
   ```bash
   curl -X POST https://rozliczenia.twojadomena.pl/ingest \
        -H "X-API-Key: $INGEST_API_KEY" \
        -F "file=@Rozliczenie_2026M07_ZZMP1.xlsx" \
        -F "source=manual"
   ```

Wgranie plików historycznych: `python -m app.cli backfill ./archiwum/`
(dbt uruchamia się raz, po wszystkich plikach).

## Kody odpowiedzi API

| Status | Znaczenie | Co robi n8n |
|---|---|---|
| `processed`, `checks_failed = 0` | wszystko w porządku | powiadomienie o sukcesie |
| `processed`, `checks_failed > 0` | dane w bazie, ale kwoty się nie spinają | alert do wyjaśnienia z nadawcą |
| `duplicate` | ten plik już był | nic — to normalne przy ponownym wysłaniu maila |
| `failed` | plik nie wszedł do bazy (uszkodzony lub inna struktura) | alert |

## Testy

```bash
pytest ingestion/tests -q          # testy parsera
docker compose exec ingest dbt test --project-dir /app/dbt --profiles-dir /app/dbt
```

## Bezpieczeństwo

- Postgres nie ma wystawionego portu — dostępny tylko w sieci Dockera.
- Metabase łączy się rolą `metabase_ro` (tylko `SELECT`). Po pierwszym starcie
  zmień jej hasło: `ALTER ROLE metabase_ro PASSWORD '…';`
- API chronione kluczem w nagłówku `X-API-Key`, panel ręczny — Basic Auth.
- Worker działa jako użytkownik bez uprawnień roota.
- Sekrety wyłącznie w zmiennych środowiskowych Coolify, nigdy w repozytorium.

## Kopie zapasowe

Usługa `backup` robi `pg_dump` codziennie o 03:15 do wolumenu `backups`
z retencją 30 dni. Wolumen `archive` zawiera oryginalne pliki Excel — to jest
właściwe źródło prawdy, z którego można odtworzyć całą bazę poleceniem
`backfill`. Warto oba wolumeny synchronizować poza serwer (rsync lub S3).
