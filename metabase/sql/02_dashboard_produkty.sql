-- =============================================================================
-- DASHBOARD 2: "Produkty" — co zarabia, co traci
-- =============================================================================


-- KARTA 2.1 [Tabela] — Top 20 produktów po zysku (ostatni okres)
-- Wizualizacja: Table, kolumna "Zysk" z paskiem (mini bar)
SELECT
    index_code       AS "Indeks",
    product_name     AS "Nazwa",
    ilosc            AS "Ilość",
    wartosc_netto    AS "Wartość netto",
    zysk             AS "Zysk",
    marza_pct        AS "Marża",
    udzial_w_zysku   AS "Udział w zysku",
    klasa_abc        AS "ABC"
FROM mart.mart_product_performance
WHERE period_id = (SELECT max(period_id) FROM mart.mart_product_performance)
ORDER BY zysk DESC
LIMIT 20;


-- KARTA 2.2 [Tabela, alert] — Produkty sprzedawane ze stratą
-- Wizualizacja: Table; ustaw alert gdy liczba wierszy > 0
SELECT
    period_label    AS "Okres",
    index_code      AS "Indeks",
    product_name    AS "Nazwa",
    ilosc           AS "Ilość",
    wartosc_netto   AS "Wartość netto",
    zysk            AS "Strata",
    marza_pct       AS "Marża"
FROM mart.mart_product_performance
WHERE sprzedane_ze_strata
  AND period_id = (SELECT max(period_id) FROM mart.mart_product_performance)
ORDER BY zysk ASC;


-- KARTA 2.3 [Wykres kołowy / słupkowy] — Koncentracja zysku (ABC)
-- Kluczowa informacja: ile SKU odpowiada za 80% zysku.
SELECT
    klasa_abc                               AS "Klasa",
    COUNT(*)                                AS "Liczba SKU",
    SUM(zysk)                               AS "Zysk",
    SUM(zysk) / SUM(SUM(zysk)) OVER ()      AS "Udział w zysku"
FROM mart.mart_product_performance
WHERE period_id = (SELECT max(period_id) FROM mart.mart_product_performance)
GROUP BY klasa_abc
ORDER BY klasa_abc;


-- KARTA 2.4 [Tabela] — Największe wzrosty i spadki miesiąc do miesiąca
-- Wizualizacja: Table z formatowaniem warunkowym (zielony/czerwony)
WITH ostatni AS (
    SELECT * FROM mart.mart_product_performance
    WHERE period_id = (SELECT max(period_id) FROM mart.mart_product_performance)
      AND zysk_poprz IS NOT NULL
)
(SELECT 'wzrost' AS "Kierunek", index_code AS "Indeks", product_name AS "Nazwa",
        zysk_poprz AS "Zysk poprzednio", zysk AS "Zysk teraz", zysk_zmiana AS "Zmiana"
   FROM ostatni ORDER BY zysk_zmiana DESC LIMIT 10)
UNION ALL
(SELECT 'spadek', index_code, product_name, zysk_poprz, zysk, zysk_zmiana
   FROM ostatni ORDER BY zysk_zmiana ASC LIMIT 10);


-- KARTA 2.5 [Wykres punktowy] — Marża vs wolumen
-- Wizualizacja: Scatter; X = wartość netto, Y = marża, rozmiar = zysk.
-- Szuka produktów o dużym obrocie i niskiej marży (kandydaci do podwyżki ceny).
SELECT
    index_code      AS "Indeks",
    product_name    AS "Nazwa",
    wartosc_netto   AS "Obrót",
    marza_pct       AS "Marża",
    zysk            AS "Zysk",
    klasa_abc       AS "Klasa"
FROM mart.mart_product_performance
WHERE period_id = (SELECT max(period_id) FROM mart.mart_product_performance)
  AND wartosc_netto > 0
ORDER BY wartosc_netto DESC
LIMIT 200;


-- KARTA 2.6 [Wykres liniowy] — Historia wybranego produktu
-- Dodaj w Metabase filtr {{indeks}} podpięty do kolumny index_code.
SELECT
    period_label   AS "Okres",
    index_code     AS "Indeks",
    ilosc          AS "Ilość",
    wartosc_netto  AS "Wartość",
    zysk           AS "Zysk",
    marza_pct      AS "Marża"
FROM mart.mart_product_performance
WHERE {{indeks}}
ORDER BY period_start;
-- [[AND index_code = {{indeks}}]]  <- wariant z filtrem opcjonalnym


-- KARTA 2.7 [Liczba] — Nowe produkty w tym okresie
SELECT COUNT(*) AS "Nowe SKU w sprzedaży"
FROM mart.mart_product_performance
WHERE nowy_w_tym_okresie
  AND period_id = (SELECT max(period_id) FROM mart.mart_product_performance);
