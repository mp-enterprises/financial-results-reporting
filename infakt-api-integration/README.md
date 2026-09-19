# Integracja inFakt API v3

Samodzielny moduł, niezależny od pipeline'u rozliczeń partnerskich
(`ingestion/`). Pobiera dane firmowe z **REST API v3 inFakt**
(`api.infakt.pl` / sandbox `api.sandbox-infakt.pl`) i zapisuje je jako pliki
JSON na dysku — bez bazy danych, bez dbt, bez Metabase. Zakres i uzasadnienie
architektury opisane są w ocenie wykonalności z 2026-09-16 (patrz sekcja
„Kontekst” niżej); ten moduł implementuje jej rekomendację: **REST API v3, nie
MCP**.

## Dlaczego osobny moduł

- Inny cel biznesowy niż `ingestion/` (dane firmowe/księgowe inFakt, nie
  rozliczenia partnerskie) i inne źródło danych (REST API, nie pliki Excel).
- Zero współdzielonych zależności: własny `requirements.txt`, własny `.env`,
  własny katalog wyjściowy `data/`. Nie modyfikuje `ingestion/`, `dbt/`,
  `docker-compose.yml`, `Makefile` ani CI.
- Można go uruchamiać, testować i wdrażać (albo nie wdrażać) całkowicie
  niezależnie od reszty repozytorium.

## Przepływ danych

```
.env (INFAKT_API_KEY, INFAKT_ENV, ...)
        │
        ▼
InfaktClient (app/client.py)
  - nagłówek X-inFakt-ApiKey
  - paginacja (max 100 rekordów/stronę)
  - ponowienia przy 429 (limit: 300 GET / 60s)
        │
        ▼
resources.py — pobiera zasoby wg okresu (YYYY-MM):
  przychód:  invoices (spółka wystawia wyłącznie faktury VAT — jedyny
             typ zdarzenia sprzedażowego; margin/advance/final/oss/
             internal_invoices świadomie NIE są pobierane)
  koszty:    documents/costs
  podatki:   insurance_fees (ZUS), income_taxes (PIT/CIT), saf_v7_files (VAT/JPK)
        │
        ▼
filtruj_po_okresie — zawęża invoices/costs do okresu po stronie klienta
(serwer jest zawodny, patrz „Ograniczenia”)
        │
        ▼
filtrowanie po źródle KSeF (obie strony — przychód i koszty):
  koszty:   filtruj_zrodlo_ksef — tylko rekordy z polem `source == "ksef"`
  faktury:  filtruj_ksef_faktury — tylko rekordy z niepustym `ksef_number`
  (rekordy spoza KSeF, np. `source == "infakt"` albo faktura bez numeru
  KSeF, są odrzucane — patrz „Ograniczenia”)
        │
        ▼
denylist.py — wyklucza tę samą listę NIP-ów z obu stron:
  koszty:   po NIP sprzedawcy (dostawcy)
  faktury:  po NIP klienta (nabywcy)
  (filtrowanie PO STRONIE KLIENTA — patrz „Ograniczenia” niżej)
        │
        ▼
aggregator.py — liczy: zysk/strata = przychód − (koszty + podatek + ZUS)
        │
        ▼
storage.py — zapis do JSON:
  data/raw/<okres>/invoices_raw.json     (invoices, bez żadnych filtrów)
  data/raw/<okres>/invoices.json         (invoices: w okresie + tylko KSeF + po denyliście NIP klienta)
  data/raw/<okres>/costs_in_period.json  (JEDYNY plik kosztów: w okresie + tylko źródło KSeF + po denyliście)
  data/raw/<okres>/insurance_fees.json, income_taxes.json, saf_v7_files.json
                                          (niefiltrowane po okresie — patrz „Ograniczenia”)
  data/summary/<okres>.json              (podsumowanie/agregat)
```

Orkiestruje to wszystko `pipeline.py::uruchom_pobieranie(okres)`, wywoływane
przez CLI (`app/cli.py`).

## Struktura katalogów

```
infakt-api-integration/
├── app/
│   ├── client.py       — klient HTTP: auth, paginacja, ponowienia, błędy
│   ├── resources.py    — mapowanie zasobów API na karty panelu Statystyki
│   ├── denylist.py      — wykluczanie kosztów po NIP dostawcy
│   ├── aggregator.py    — wzór zysk/strata, sumowanie kwot
│   ├── storage.py       — zapis JSON
│   ├── pipeline.py      — orkiestracja end-to-end
│   ├── cli.py            — `python -m app.cli ...`
│   └── config.py         — Settings z .env
├── tests/                — 55 testów, HTTP mockowane przez `responses`
├── data/                  — wynik działania (gitignored, tworzone automatycznie)
├── .env.example           — szablon (tracked)
├── .env                   — Twoje realne wartości (gitignored, NIE commitować)
└── requirements.txt
```

## Konfiguracja

Skopiuj `.env.example` do `.env` (już zrobione w tym repo — plik `.env`
istnieje z pustymi/domyślnymi wartościami poza kluczem, który wygenerujesz w
panelu inFakt: **Ustawienia → Integracje → API**, z zakresami
`api:invoices:read`, `api:costs:read`, `api:accounting:read`).

| Zmienna | Domyślna | Opis |
|---|---|---|
| `INFAKT_API_KEY` | *(puste)* | klucz API — **WYMAGANY** |
| `INFAKT_ENV` | `sandbox` | `sandbox` albo `production` |
| `INFAKT_OUTPUT_DIR` | `./data` | katalog wyjściowy JSON |
| `INFAKT_MAX_RETRIES` | `5` | ponowienia przy HTTP 429 |
| `INFAKT_TIMEOUT_S` | `30` | timeout pojedynczego zapytania |
| `INFAKT_SUPPLIER_DENYLIST_NIP` | *(puste)* | NIP-y dostawców do wykluczenia z kosztów, rozdzielone przecinkiem |

**Uwaga po pierwszym uruchomieniu z realnym kluczem:** klucz obecnie w `.env`
uwierzytelnia się poprawnie w środowisku **`production`**
(`api.infakt.pl`), ale zwraca **401 w `sandbox`**
(`api.sandbox-infakt.pl`) — to najpewniej klucz wygenerowany z konta
produkcyjnego, a sandbox wymaga osobnego konta/klucza testowego. Efekt: domyślny
`INFAKT_ENV=sandbox` w `.env.example` **nie zadziała** z tym kluczem — trzeba
albo ustawić `INFAKT_ENV=production` w `.env`, albo użyć `--env production`
przy każdym wywołaniu CLI, albo założyć osobne konto sandbox w inFakt i
wygenerować dla niego osobny klucz. `fetch` uruchomiony na `production`
pobiera **realne dane finansowe firmy** — traktuj `data/` odpowiednio
(katalog jest gitignored, ale to nadal wrażliwe dane na dysku).

## Użycie

```bash
cd infakt-api-integration
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# sprawdź, czy klucz działa (1 lekkie zapytanie GET, bez zapisu plików)
python -m app.cli test-connection
python -m app.cli --env production test-connection   # patrz uwaga wyżej

# pobierz dane za dany miesiąc i zapisz do JSON
python -m app.cli fetch --okres 2026-09
python -m app.cli --env production fetch --okres 2026-09 --output-dir ./data
```

Kody wyjścia: `0` sukces, `1` błąd (zły klucz, błąd sieci, nieprawidłowy
format okresu).

## Testy

```bash
cd infakt-api-integration
source .venv/bin/activate
python -m pytest tests -q
```

63 testy, wszystkie z mockowanym HTTP (biblioteka `responses`) — **nie
wymagają realnego klucza API** ani dostępu do sieci. Pokrywają: nagłówek
autoryzacji, obsługę 401/429/500, paginację przez `offset`/`metainfo.total_count`
(w tym regresję na `Retry-After: 0` i na serwer ignorujący żądany `limit`),
zabezpieczenie przed nieskończoną pętlą paginacji, filtrowanie po okresie po
stronie klienta, filtrowanie denylistą (w tym zachowanie fail-open przy
nierozpoznanym polu NIP), wzór zysk/strata, zapis plików JSON (w tym polskie
znaki), CLI (`test-connection`, `fetch`) i parsowanie okresu (`YYYY-MM` →
zakres dat, w tym walidacja grudnia/stycznia i błędnych miesięcy).

## Kontekst: ocena wykonalności (2026-09-16)

Ten moduł implementuje rekomendację z oceny wykonalności zleconej przez
Piotra Siemińskiego: budować na **REST API v3**, nie na oficjalnym ani
nieoficjalnym MCP Server — MCP jest zaprojektowany pod rozmowę z asystentem
AI, nie pod deterministyczny, harmonogramowany pipeline danych.

Ocena zidentyfikowała formułę panelu Statystyki (potwierdzoną w artykule
pomocy inFakt):

```
zysk/strata = przychód − (koszty + podatek dochodowy + ZUS)
```

i zmapowała ją na zasoby API — to odzwierciedla `app/aggregator.py` i
`app/resources.py`.

## Ograniczenia i co jest potwierdzone na żywo (2026-09-16)

Moduł został uruchomiony na koncie **produkcyjnym** dla okresów 2026-06,
2026-07 i 2026-08 (klucz propagowany do `.env` przez Piotra). To ujawniło
kilka rozbieżności między oceną wykonalności (opartą o dokumentację i
nieoficjalne SDK) a rzeczywistym zachowaniem API — poniżej stan faktyczny,
nie tylko przypuszczenia.

**Potwierdzone i naprawione:**

1. **Paginacja jest przez `offset`/`limit`, nie `page`/`total_pages`.**
   Realna odpowiedź: `{"metainfo": {"count", "total_count", "next",
   "previous"}, "entities": [...]}`. Serwer bywa, że ignoruje żądany `limit`
   (np. `invoices.json` zwracało 10 rekordów mimo `limit=100`) — `offset`
   przesuwamy więc o faktyczną liczbę zwróconych rekordów, nie o żądany
   rozmiar strony. `next`/`previous` bywają obecne nawet za granicą
   `total_count`, więc nie służą jako warunek stopu. `get_all_pages()` ma
   też twardy limit stron (`max_pages`, domyślnie 500) jako zabezpieczenie.
2. **`Retry-After` bywa `0`.** Pierwsze uruchomienie na produkcji trafiło w
   limit zapytań i weszło w pętlę natychmiastowych ponowień (429 → 0s
   odczekania → 429 → ...), aż wyczerpało wszystkie próby w ułamku sekundy.
   Naprawione: `wait_s = max(2**próba, Retry-After)` — własny backoff
   wykładniczy jest teraz podłogą, nie tylko fallbackiem gdy nagłówek
   brakuje.
3. **Filtrowanie datą na serwerze NIE działa dla `documents/costs.json`** —
   potwierdzone bezpośrednio: zapytanie z `issue_date_gteq`/`issue_date_lt`
   (płaskie) zwracało identyczny `total_count` i identyczne rekordy jak bez
   filtra; zagnieżdżona konwencja Ransack `q[issue_date_gteq]=...` zmieniała
   `total_count` w metadanych, ale zwracane rekordy pozostawały te same
   (dane spoza żądanego zakresu) — czyli parametr jest niewiarygodny w obu
   wariantach. **Rozwiązanie: filtrowanie po dacie jest teraz zawsze
   wykonywane też po stronie klienta** (`resources.filtruj_po_okresie`),
   niezależnie od tego, czy serwer coś odfiltrował. Poprawność już nie
   zależy od zaufania do parametru zapytania.
4. **Pole daty dla kosztów to `issue_date`, nie `invoice_date`** (rekordy
   kosztowe w ogóle nie mają pola `invoice_date`) — potwierdzone na żywo.
   Dla sześciu typów faktur przychodowych `invoice_date` jest potwierdzone
   dla `invoices.json`; dla pozostałych pięciu wariantów (margin/advance/
   final/oss/internal) przyjęto tę samą nazwę przez analogię kształtu
   zasobu — nie zweryfikowane osobno dla każdego wariantu.
5. **Kwoty w `invoices.json` są w groszach pod `gross_price`/`net_price`/
   `tax_price`** — potwierdzone na żywo (zgadza się z polem
   `amount_in_words` na tym samym rekordzie).

**Nadal niepewne / świadomie niedoszacowane:**

6. **Kwoty w `insurance_fees.json` (ZUS)** — prawdziwe pole to
   `sum_amount_price` (potwierdzone na żywo), ale jego skala jest niejasna:
   jeden rekord miał `sum_amount_price=9233`, co nie pasuje przekonująco ani
   jako 92,33 PLN (grosze) ani jako 9233 PLN za pojedynczy okres, a konto ma
   18 rekordów `insurance_fees` na raptem kilka miesięcy — prawdopodobnie to
   sumy per SKŁADNIK ZUS (emerytalne/rentowe/zdrowotne/FP osobno), nie per
   okres. Celowo **nie** dodano tego pola do `aggregator.POLA_KWOTY_BRUTTO`
   — zgadywanie skali ryzykowałoby cichym zepsuciem wyniku; `zus` w
   podsumowaniu zostaje `"0.00"` z tym jawnym zastrzeżeniem, zamiast liczby,
   której nie da się zweryfikować. Surowe rekordy są zapisane w
   `data/raw/<okres>/insurance_fees.json` do ręcznej inspekcji.
7. **`income_taxes.json` i `saf_v7_files.json` zwróciły 0 rekordów** dla
   wszystkich trzech testowanych miesięcy na koncie produkcyjnym. Nie wiadomo,
   czy to odzwierciedla rzeczywistość konta (np. brak zaksięgowanych
   deklaracji), brak uprawnień zakresu `api:accounting:read` dla tych
   konkretnych zasobów, czy zła ścieżka endpointu — wymaga sprawdzenia
   bezpośrednio w panelu inFakt.
8. **`insurance_fees`/`income_taxes`/`saf_v7_files` NIE są filtrowane po
   okresie po stronie klienta** (pole daty per-okres niepotwierdzone — ZUS ma
   `period_name` typu `"Wrzesień 2026"`, nie pole daty w formacie ISO). Każde
   wywołanie `fetch` pobiera dla nich to samo, niefiltrowane query — dla
   `insurance_fees` to cała historia konta (18 identycznych rekordów w każdym
   z trzech testowanych miesięcy). Podsumowanie oznacza to jawnie polem
   `"zus_podatki_jpk_bez_filtrowania_okresu": true`.
9. **Pole z NIP sprzedawcy w kosztach** — potwierdzone na żywo (konto
   produkcyjne, 2026-09-17): to `seller_tax_code` (nie
   `seller_tax_code_number`, który był tylko przypuszczeniem z nieoficjalnego
   Go SDK; oba pola są nadal na liście `POLA_NIP_SPRZEDAWCY`, `seller_tax_code`
   po prostu wygrywa jako pierwsze rzeczywiście obecne). Rekordy bez
   rozpoznanego pola są fail-open — patrz `kosztow_bez_rozpoznanego_nip` w
   podsumowaniu.
10. **Rozróżnienie KPiR/ryczałt (amortyzacja) i status
    zapłacone/niezapłacone/robocze** — panel Statystyki to rozróżnia, ten
    moduł sumuje wszystkie pobrane rekordy bez rozbicia po statusie (pole
    `status` istnieje na rekordach ZUS, np. `"draft"` — do wykorzystania,
    jeśli rozróżnienie stanie się potrzebne).
11. **Wykluczanie po NIP jest realizowane po stronie klienta**, nie jako
    natywny filtr API — świadoma decyzja z oceny wykonalności, nie
    ograniczenie tego kodu.
12. **Pole `source` w `documents/costs.json`** — potwierdzone na żywo
    (2026-09-17): wartość `"ksef"` (koszt wczytany przez Krajowy System
    e-Faktur) vs `"infakt"` (dodany inną drogą w panelu). Od 2026-09-17
    `costs_in_period.json` zawiera wyłącznie koszty źródła KSeF — koszty
    `"infakt"` są liczone i widoczne w podsumowaniu jako
    `kosztow_odrzuconych_zrodlo_nie_ksef`, ale nie wchodzą do
    `koszty`/`zysk_strata`.
13. **`invoices.json` nie ma pola `source`** — jego odpowiednikiem jest
    `ksef_number` (potwierdzone na żywo, 2026-09-18): niepusty oznacza, że
    faktura trafiła do KSeF. Od 2026-09-18 `invoices.json` zawiera wyłącznie
    faktury z niepustym `ksef_number`; te bez numeru KSeF są odrzucone i
    zliczone w podsumowaniu jako `faktur_odrzuconych_zrodlo_nie_ksef`
    (per zasób).
14. **Denylist stosowana też do faktur przychodowych, po NIP klienta**
    (`client_tax_code`, potwierdzone na żywo, 2026-09-18) — ta sama lista
    `INFAKT_SUPPLIER_DENYLIST_NIP` wyklucza teraz dany podmiot zarówno jako
    dostawcę kosztów, jak i jako nabywcę na fakturze sprzedażowej. Odrzucone
    rekordy są zliczone jako `faktur_odrzuconych_denylist` (per zasób), z tym
    samym fail-open dla nierozpoznanego NIP co przy kosztach
    (`faktur_bez_rozpoznanego_nip_klienta`).

Żaden z powyższych punktów nie blokuje działania kodu — wszystkie awarie są
albo jawnym błędem (`InfaktApiError` z treścią odpowiedzi), albo policzalnym
ostrzeżeniem w podsumowaniu (fail-open + licznik), nigdy cichym zerowaniem bez
śladu. **Traktuj `przychod` i `koszty` w `summary/<okres>.json` jako
wiarygodne (zweryfikowane na żywo); traktuj `zus` i `podatek_dochodowy` jako
placeholder do dalszej weryfikacji, nie jako gotowe liczby.**

## Co NIE jest w zakresie tego modułu

- Zapis danych do bazy / dbt / Metabase — na razie tylko pliki JSON na dysku,
  zgodnie z zakresem zlecenia.
- Harmonogramowanie (cron/n8n) — `python -m app.cli fetch --okres ...`
  uruchamiane jest ręcznie; podłączenie pod harmonogram to osobna decyzja.
- Oficjalny/nieoficjalny MCP Server — świadomie pominięty, patrz „Kontekst”.
