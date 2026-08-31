# System rozliczeń partnerskich

Automatyczne przetwarzanie miesięcznych rozliczeń przychodzących mailem w formie
pliku Excel: odbiór → walidacja → baza relacyjna → transformacje → dashboardy.

```
Mail z załącznikiem .xlsx
        │
        ▼
  ┌───────────┐   HTTPS + X-API-Key    ┌──────────────────────────┐
  │ n8n Cloud │ ─────────────────────► │  worker "ingest"         │
  │   IMAP    │ ◄───── odpytywanie ─── │  parser → loader → dbt   │
  └───────────┘   GET /files/{sha}     └────────────┬─────────────┘
                                                    │
   formularz ręcznego uploadu ──────────────────►   ▼
   (https://rozliczenia.domena.pl)        ┌──────────────────┐
                                          │   PostgreSQL     │
                                          │ raw / core / mart│
                                          └────────┬─────────┘
                                                   ▼
                                             ┌──────────┐
                                             │ Metabase │
                                             └──────────┘
```

Na serwerze Coolify stoją cztery kontenery: `postgres`, `ingest`, `metabase`
i `backup`. n8n działa w chmurze i łączy się z workerem wyłącznie przez
publiczny HTTPS — dlatego baza nigdy nie jest wystawiona na świat.

## Dlaczego tak

**Trzy warstwy w bazie.** `raw` przechowuje niezmienny zrzut każdego arkusza
w JSONB — jeśli za pół roku okaże się, że parser źle interpretował jakąś
kolumnę, dane da się przeliczyć bez proszenia nadawcy o ponowne przesłanie
plików. `core` to znormalizowany model relacyjny. `mart` to gotowe tabele
analityczne budowane przez dbt.

**Idempotencja na trzech poziomach.** Skrót SHA-256 zawartości pliku jest
`UNIQUE` — identyczny plik nigdy nie zostanie przetworzony dwa razy. Klucz
naturalny `(partner, okres, kanał)` z indeksem częściowym `WHERE is_current`
gwarantuje jedno aktualne rozliczenie na okres. Ładowanie pozycji odbywa się
w schemacie DELETE + INSERT w jednej transakcji.

**Korekty nie nadpisują historii.** Poprawiona wersja pliku za ten sam miesiąc
tworzy rewizję nr 2, a poprzednia dostaje `is_current = FALSE` i wskazanie
`superseded_by`. Widoki analityczne pokazują tylko wersję aktualną, audyt zostaje.

**Nie ufamy arytmetyce w pliku.** Po każdym załadowaniu system niezależnie
przelicza sześć równań rozliczenia. Rozbieżność powyżej grosza to alert,
nie cichy błąd w raporcie.

**Parsowanie po etykietach — wierszy i kolumn.** Ani wstawienie wiersza, ani
wstawienie czy przestawienie kolumny nie psuje procesu: pozycje w tabelach
`Raport` i `Stok` są odczytywane przez mapę zbudowaną z nagłówka, nie po stałych
indeksach. To zabezpieczenie przed najgroźniejszą zmianą, jaką nadawca może
wprowadzić — odczyt po pozycjach przesunąłby wartości i załadował do bazy ciche
przekłamania (cena jako wartość, wartość jako zysk), bez żadnego błędu.
Brak kolumny wymaganej to kwarantanna, brak opcjonalnej — ostrzeżenie.

**Nowe arkusze nie psują niczego.** Arkusz, którego parser nie zna, jest
archiwizowany w `raw.sheet_payload` i zgłaszany w `warnings`, ale nie wpływa na
rozliczenie. Odczyt takiego arkusza jest ograniczony do 20 000 wierszy, żeby
załącznik o nieoczekiwanej wielkości nie wyczerpał pamięci workera.

**Arkusze opcjonalne.** Wymagane są tylko `Karta` i `Raport`. `Karta_MJ`
(kanał Amazon) i `Stok` (magazyn) bywają nieobecne — nadawca dodał arkusz
`Stok` dopiero od 2026 M07, więc starsze rozliczenia go nie mają. Brak
opcjonalnego arkusza daje ostrzeżenie w polu `warnings`, nie błąd, i nigdy
nie kasuje danych magazynowych wczytanych wcześniej z innego pliku.

**Przetwarzanie asynchroniczne.** `POST /ingest` z `mode=async` odpowiada
natychmiast kodem 202 i pracuje w tle; n8n odpytuje `GET /files/{sha}`.
Dzięki temu limit 100 sekund na proxy Cloudflare nie ma znaczenia,
a webhook nigdy nie wisi w oczekiwaniu na dbt.

## Struktura repozytorium

```
db/migrations/001_init.sql            schemat raw / core / ops + rola tylko-do-odczytu
db/init/                              tworzenie bazy pomocniczej dla Metabase
ingestion/app/
  parser.py                           Excel → struktury danych (najbardziej krytyczny plik)
  loader.py                           struktury → Postgres + rekoncyliacja
  pipeline.py                         orkiestracja i idempotencja
  api.py                              FastAPI: webhook, tryb async, formularz ręczny
  cli.py                              uruchamianie z linii poleceń
ingestion/tests/                      testy parsera (30 przypadków, w tym odporność na zmiany formatu)
dbt/models/staging/                   widoki normalizujące
dbt/models/marts/                     6 tabel analitycznych
n8n/workflow_rozliczenia_cloud.json   workflow do zaimportowania w n8n Cloud
metabase/sql/                         29 zweryfikowanych zapytań pod karty dashboardów
docker-compose.yml                    stack dla Coolify
docs/wdrozenie.html                   runbook wdrożeniowy krok po kroku
docs/onboarding_partnera.html         procedura dodania kolejnego partnera
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
bo to dwa oddzielne przelewy od dwóch różnych podmiotów. Model
`mart_partner_pnl` konsoliduje je z zachowaniem rozbicia.

### Tabele analityczne

| Model | Do czego służy |
|---|---|
| `mart_settlement_monthly` | rachunek per kanał, dynamika MoM, kolumny kontrolne |
| `mart_partner_pnl` | skonsolidowany wynik, rentowność, rotacja kapitału |
| `mart_product_performance` | rentowność SKU, klasyfikacja ABC, wzrosty i spadki |
| `mart_stock_health` | kapitał zamrożony, dni pokrycia, rekomendacje zakupowe |
| `mart_cost_structure` | struktura kosztów w formacie długim (pod wykresy) |
| `mart_pipeline_health` | stan procesu: co dotarło, co się nie udało |

Każda tabela w warstwie `mart` niesie `partner_id` i `partner_code`, a wszystkie
funkcje okna partycjonują po partnerze — model jest wielo-partnerski od początku
i dodanie kolejnego podmiotu nie wymaga migracji. Karty w `metabase/sql/`
wymagają zmiennej `{{partner}}`: bez niej przy dwóch partnerach mieszałyby dane
dwóch firm bez żadnego sygnału błędu. Pełna procedura: `docs/onboarding_partnera.html`.

## API

Endpointy maszynowe wymagają nagłówka `X-API-Key`, administracyjne — Basic Auth.

| Metoda | Ścieżka | Opis |
|---|---|---|
| `GET` | `/healthz` | healthcheck (bez autoryzacji) |
| `POST` | `/ingest` | wgranie pliku; `mode=sync` (domyślnie) lub `mode=async` |
| `GET` | `/files/{sha}` | stan przetwarzania pliku — do odpytywania w trybie async |
| `GET` | `/status/okres` | czy rozliczenie za dany miesiąc jest w bazie |
| `POST` | `/dbt/run` | ręczne przeliczenie modeli |
| `GET` | `/` | formularz ręcznego wgrania (Basic Auth) |
| `GET` | `/runs`, `/files`, `/checks` | podgląd stanu (Basic Auth) |

### Odpowiedzi `/ingest`

| Status | Kod | Znaczenie |
|---|---|---|
| `processed` | 200 | plik załadowany (sprawdź `checks_failed`) |
| `accepted` | 202 | przyjęty do przetwarzania w tle (tryb async) |
| `duplicate` | 200 | ten plik już był — nic się nie dzieje |
| `failed` | 422 | plik nie wszedł do bazy |

## Domeny

Domeny ustawiasz w UI Coolify, w polu **Domains** przy każdej usłudze, razem
z portem kontenera:

| Usługa | Wartość w polu Domains |
|---|---|
| `metabase` | `https://bi.twojadomena.pl:3000` |
| `ingest` | `https://rozliczenia.twojadomena.pl:8000` |
| `postgres`, `backup` | puste — tylko sieć wewnętrzna |

W Cloudflare potrzebne są dwa rekordy A wskazujące na IP serwera, a tryb
SSL/TLS musi być ustawiony na **Full (strict)**. Pełna procedura z kolejnością
kroków jest w `docs/wdrozenie.html`, krok 04.

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

## Testy

```bash
pytest ingestion/tests -q          # 30 testów parsera
docker compose exec ingest dbt test --project-dir /app/dbt --profiles-dir /app/dbt
```

## Bezpieczeństwo

- Postgres nie ma wystawionego portu — dostępny tylko w wewnętrznej sieci
  projektu, którą tworzy Coolify (compose celowo nie definiuje własnej sieci,
  bo to odcina usługi od proxy Traefika).
  n8n Cloud nigdy nie łączy się z bazą; pyta worker przez `/status/okres`.
- Metabase łączy się rolą `metabase_ro` (tylko `SELECT`). Po pierwszym starcie
  zmień jej hasło: `ALTER ROLE metabase_ro PASSWORD '…';`
- API chronione kluczem w nagłówku `X-API-Key`, panel ręczny — Basic Auth.
- Worker działa jako użytkownik bez uprawnień roota.
- Sekrety wyłącznie w zmiennych środowiskowych Coolify, nigdy w repozytorium.

## Kopie zapasowe

Usługa `backup` robi `pg_dump` codziennie o 03:15 do wolumenu `backups`
z retencją 30 dni. Wolumen `archive` zawiera oryginalne pliki Excel — to jest
właściwe źródło prawdy, z którego można odtworzyć całą bazę poleceniem
`backfill`. Warto oba wolumeny synchronizować poza serwer.
