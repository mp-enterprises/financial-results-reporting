-- =============================================================================
-- FILTR PARTNERA — obowiązkowy w każdej karcie
--
-- W Metabase dodaj do każdej karty zmienną tekstową {{partner}}:
--   Variables -> partner -> Variable type: Text -> Required -> Default: ZZMP1
--
-- Dlaczego obowiązkowy, a nie opcjonalny: przy dwóch partnerach karta bez
-- filtra po cichu miesza dane dwóch firm na jednym wykresie albo pokazuje
-- wynik nie tego podmiotu, o który pytasz — bez żadnego sygnału błędu.
--
-- OSTATNI OKRES liczymy po period_start (prawdziwej dacie), NIGDY po
-- max(period_id). period_id to klucz sztuczny nadawany w kolejności wczytania
-- plików — po wgraniu historii wstecz wskazuje niewłaściwy miesiąc.
-- =============================================================================

-- =============================================================================
-- DASHBOARD 3: "Magazyn" — kapitał zamrożony i ryzyko braków
-- To jest dashboard, na którym najszybciej widać pieniądze do odzyskania.
-- =============================================================================


-- KARTA 3.1 [Liczby] — Stan magazynu w skrócie
SELECT
    SUM(wartosc_zakupu)                                       AS "Wartość magazynu",
    SUM(wartosc_zakupu) FILTER (WHERE flaga_martwy)           AS "Kapitał w martwym stoku",
    SUM(kapital_nadmiarowy)                                   AS "Kapitał nadmiarowy",
    COUNT(*)                                                  AS "Pozycji (SKU)",
    COUNT(*) FILTER (WHERE flaga_dozamowic)                   AS "Do dozamówienia"
FROM mart.mart_stock_health
WHERE partner_code = {{partner}}
  AND period_start = (SELECT max(period_start) FROM mart.mart_stock_health
                            WHERE partner_code = {{partner}});


-- KARTA 3.2 [Wykres kołowy] — Struktura magazynu wg rotacji
-- Wizualizacja: Pie / Donut po wartości, nie po liczbie SKU.
SELECT
    kategoria_rotacji       AS "Kategoria",
    COUNT(*)                AS "Liczba SKU",
    SUM(wartosc_zakupu)     AS "Wartość zakupu",
    SUM(stan_szt)           AS "Sztuk"
FROM mart.mart_stock_health
WHERE partner_code = {{partner}}
  AND period_start = (SELECT max(period_start) FROM mart.mart_stock_health
                            WHERE partner_code = {{partner}})
GROUP BY kategoria_rotacji
ORDER BY SUM(wartosc_zakupu) DESC;


-- KARTA 3.3 [Tabela, priorytet] — Największy zamrożony kapitał
-- Sortowanie po wartości: to lista „gdzie leżą pieniądze”.
SELECT
    index_code       AS "Indeks",
    product_name     AS "Nazwa",
    stan_szt         AS "Stan",
    wartosc_zakupu   AS "Wartość zakupu",
    sprzedaz_3m      AS "Sprzedaż 3 mies.",
    wystarczy_na_dni AS "Wystarczy na (dni)",
    rekomendacja     AS "Rekomendacja"
FROM mart.mart_stock_health
WHERE partner_code = {{partner}}
  AND period_start = (SELECT max(period_start) FROM mart.mart_stock_health
                            WHERE partner_code = {{partner}})
  AND flaga_martwy
ORDER BY wartosc_zakupu DESC
LIMIT 30;


-- KARTA 3.4 [Tabela, pilne] — Do dozamówienia
-- Wizualizacja: Table; posortowane wg pilności.
SELECT
    index_code                AS "Indeks",
    product_name              AS "Nazwa",
    stan_szt                  AS "Stan",
    srednia_dzienna           AS "Śr. dziennie",
    wystarczy_na_dni          AS "Wystarczy na (dni)",
    sprzedaz_miesiac          AS "Sprzedaż w miesiącu",
    sprzedaz_zysk             AS "Zysk w miesiącu",
    -- ile sztuk zamówić, żeby mieć zapas na 90 dni
    CEIL(GREATEST(srednia_dzienna * 90 - stan_szt, 0))  AS "Sugerowane zamówienie (90 dni)",
    rekomendacja              AS "Rekomendacja"
FROM mart.mart_stock_health
WHERE partner_code = {{partner}}
  AND period_start = (SELECT max(period_start) FROM mart.mart_stock_health
                            WHERE partner_code = {{partner}})
  AND flaga_dozamowic
ORDER BY wystarczy_na_dni ASC NULLS LAST
LIMIT 50;


-- KARTA 3.5 [Wykres punktowy] — Rotacja vs rentowność
-- X = dni pokrycia (log), Y = marża. Lewy górny róg = towar idealny.
SELECT
    index_code           AS "Indeks",
    product_name         AS "Nazwa",
    wystarczy_na_dni     AS "Dni pokrycia",
    sprzedaz_marza_pct   AS "Marża",
    wartosc_zakupu       AS "Wartość na stanie",
    kategoria_rotacji    AS "Kategoria"
FROM mart.mart_stock_health
WHERE partner_code = {{partner}}
  AND period_start = (SELECT max(period_start) FROM mart.mart_stock_health
                            WHERE partner_code = {{partner}})
  AND wystarczy_na_dni IS NOT NULL
  AND sprzedaz_marza_pct IS NOT NULL
ORDER BY wartosc_zakupu DESC
LIMIT 300;


-- KARTA 3.6 [Wykres liniowy] — Trend wartości magazynu i martwego stoku
SELECT
    period_label                                     AS "Okres",
    SUM(wartosc_zakupu)                              AS "Wartość magazynu",
    SUM(wartosc_zakupu) FILTER (WHERE flaga_martwy)  AS "Martwy stok",
    SUM(kapital_nadmiarowy)                          AS "Kapitał nadmiarowy"
FROM mart.mart_stock_health
WHERE partner_code = {{partner}}
GROUP BY period_label, period_start
ORDER BY period_start;


-- KARTA 3.7 [Tabela] — Podsumowanie rekomendacji (lista zadań)
SELECT
    rekomendacja          AS "Rekomendacja",
    COUNT(*)              AS "Liczba SKU",
    SUM(wartosc_zakupu)   AS "Wartość zakupu",
    SUM(stan_szt)         AS "Sztuk"
FROM mart.mart_stock_health
WHERE partner_code = {{partner}}
  AND period_start = (SELECT max(period_start) FROM mart.mart_stock_health
                            WHERE partner_code = {{partner}})
GROUP BY rekomendacja
ORDER BY SUM(wartosc_zakupu) DESC;
