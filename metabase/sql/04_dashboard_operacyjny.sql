-- =============================================================================
-- DASHBOARD 4: "Proces" — czy dane w ogóle są wiarygodne
-- Dashboard dla administratora: czy pliki dochodzą, ładują się i się spinają.
-- =============================================================================


-- KARTA 4.1 [Tabela] — Historia przetwarzania plików
SELECT
    odebrano                 AS "Odebrano",
    file_name                AS "Plik",
    period_label             AS "Okres",
    zrodlo                   AS "Źródło",
    ocena                    AS "Ocena",
    kontroli_nieudanych      AS "Nieudane kontrole",
    czas_przetwarzania_s     AS "Czas (s)",
    blad                     AS "Błąd"
FROM mart.mart_pipeline_health
ORDER BY odebrano DESC
LIMIT 50;


-- KARTA 4.2 [Liczba, alert] — Pliki, które nie weszły do bazy
-- Ustaw alert: powiadom, gdy > 0.
SELECT COUNT(*) AS "Pliki z błędem"
FROM mart.mart_pipeline_health
WHERE status_pliku IN ('failed', 'quarantined');


-- KARTA 4.3 [Tabela] — Szczegóły nieudanych kontroli kwot
SELECT
    p.period_label       AS "Okres",
    s.channel_code       AS "Kanał",
    c.check_name         AS "Kontrola",
    c.expected           AS "Oczekiwano",
    c.actual             AS "Jest w pliku",
    c.difference         AS "Różnica",
    c.severity           AS "Waga",
    c.checked_at         AS "Sprawdzono"
FROM ops.data_quality_check c
LEFT JOIN core.settlement s ON s.settlement_id = c.settlement_id
LEFT JOIN core.period p     ON p.period_id = s.period_id
WHERE NOT c.passed
ORDER BY c.checked_at DESC
LIMIT 100;


-- KARTA 4.4 [Liczba, alert] — Czy rozliczenie za poprzedni miesiąc dotarło
-- Ustaw alert: powiadom, gdy wartość = 0 po 10. dniu miesiąca.
-- Rozbite na partnerów: przy kilku podmiotach jedna liczba zbiorcza ukryłaby
-- fakt, że brakuje rozliczenia tylko od jednego z nich.
SELECT
    partner_code                AS "Partner",
    COUNT(*)                    AS "Rozliczeń za poprzedni miesiąc"
FROM mart.mart_settlement_monthly
WHERE period_year  = EXTRACT(YEAR  FROM now() - INTERVAL '1 month')
  AND period_month = EXTRACT(MONTH FROM now() - INTERVAL '1 month')
GROUP BY partner_code
ORDER BY partner_code;


-- KARTA 4.5 [Tabela] — Korekty: okresy z więcej niż jedną wersją pliku
-- Jeśli tu coś jest, znaczy że nadawca przysłał poprawione rozliczenie.
SELECT
    pa.partner_code           AS "Partner",
    p.period_label            AS "Okres",
    s.channel_code            AS "Kanał",
    s.revision                AS "Rewizja",
    s.is_current              AS "Aktualna",
    f.file_name               AS "Plik",
    s.do_zaplaty              AS "Do zapłaty",
    s.created_at              AS "Wczytano"
FROM core.settlement s
JOIN core.period p          ON p.period_id = s.period_id
JOIN core.partner pa        ON pa.partner_id = s.partner_id
JOIN raw.ingested_file f    ON f.file_id = s.file_id
WHERE (s.partner_id, s.period_id, s.channel_code) IN (
    SELECT partner_id, period_id, channel_code
    FROM core.settlement GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
)
ORDER BY pa.partner_code, p.period_label DESC, s.channel_code, s.revision;


-- KARTA 4.6 [Tabela] — Rozbieżność Raport vs Karta
-- Zgodnie z instrukcją nadawcy Raport nie musi zgadzać się co do grosza
-- z Kartą (zwroty rozliczane wartościowo). Monitorujemy skalę rozbieżności.
SELECT
    partner_code                     AS "Partner",
    period_label                     AS "Okres",
    saldo_towaru                     AS "Saldo wg Karty",
    raport_total_wartosc             AS "Suma wg Raportu",
    roznica_raport_vs_karta          AS "Różnica",
    CASE WHEN saldo_towaru <> 0
         THEN roznica_raport_vs_karta / saldo_towaru END AS "Różnica %",
    roznica_zysku_raport_vs_karta    AS "Różnica zysku"
FROM mart.mart_settlement_monthly
WHERE channel_code = 'MAIN'
ORDER BY partner_code, period_start DESC;


-- KARTA 4.7 [Tabela] — Ostatnie uruchomienia pipeline'u
SELECT
    started_at    AS "Start",
    trigger       AS "Wyzwalacz",
    status        AS "Status",
    dbt_status    AS "dbt",
    rows_sales    AS "Pozycji sprzedaży",
    rows_stock    AS "Pozycji magazynu",
    ROUND(EXTRACT(EPOCH FROM (finished_at - started_at))::numeric, 1) AS "Czas (s)",
    LEFT(message, 200) AS "Komunikat"
FROM ops.pipeline_run
ORDER BY started_at DESC
LIMIT 30;
