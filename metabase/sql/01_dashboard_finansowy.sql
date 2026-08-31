-- =============================================================================
-- DASHBOARD 1: "Pieniądze" — przegląd finansowy
-- Każde zapytanie = jedna karta w Metabase. Nazwa w komentarzu = tytuł karty.
-- Filtr {{okres}} jest opcjonalny — w Metabase dodaj go jako zmienną pola.
-- =============================================================================


-- KARTA 1.1 [Liczba] — Do zapłaty w ostatnim okresie
-- Wizualizacja: Number, format PLN, porównanie do poprzedniego okresu
SELECT
    do_zaplaty_razem  AS "Do zapłaty (razem)",
    do_zaplaty_main   AS "Kanał główny (NIKCORP)",
    do_zaplaty_mj     AS "Kanał MJ / Amazon"
FROM mart.mart_partner_pnl
ORDER BY period_start DESC
LIMIT 1;


-- KARTA 1.2 [Liczba] — Faktyczny zarobek w ostatnim okresie
-- To NIE to samo co "do zapłaty": kwota przelewu zawiera też zwrot za towar.
-- Wizualizacja: Number + Trend
SELECT
    zarobek_partnera           AS "Zarobek partnera",
    zwrot_kosztu_towaru        AS "w tym zwrot kosztu towaru",
    rentownosc_netto           AS "Rentowność netto",
    zarobek_narastajaco_rok    AS "Narastająco w roku"
FROM mart.mart_partner_pnl
ORDER BY period_start DESC
LIMIT 1;


-- KARTA 1.3 [Wykres słupkowo-liniowy] — Obrót, zarobek i rentowność w czasie
-- Wizualizacja: Combo — słupki: obrót i zarobek; linia: rentowność (oś prawa, %)
SELECT
    period_label                 AS "Okres",
    obrot_netto                  AS "Obrót netto",
    zarobek_partnera             AS "Zarobek partnera",
    koszty_razem                 AS "Koszty razem",
    rentownosc_netto             AS "Rentowność netto"
FROM mart.mart_partner_pnl
ORDER BY period_start;


-- KARTA 1.4 [Wodospad / tabela] — Od obrotu do zarobku (ostatni okres)
-- Pokazuje, gdzie „znikają” pieniądze między sprzedażą a kieszenią partnera.
-- Wizualizacja: Waterfall (kolumna Krok / Kwota)
WITH ostatni AS (
    SELECT * FROM mart.mart_partner_pnl ORDER BY period_start DESC LIMIT 1
)
SELECT krok AS "Krok", kwota AS "Kwota", kolejnosc
FROM ostatni, LATERAL (VALUES
    (1, 'Zysk ze sprzedaży (po zwrotach)',      zysk_ze_sprzedazy),
    (2, 'Koszty ogólne',                        -koszty_ogolne),
    (3, 'Prowizje platform',                    -koszty_platformy),
    (4, 'Transport',                            -koszty_transport),
    (5, 'Pozostałe koszty indywidualne',        -(koszty_indywidualne - koszty_platformy - koszty_transport)),
    (6, 'Wynagrodzenie operatora',              -prowizja_operatora),
    (7, 'ZAROBEK PARTNERA',                     zarobek_partnera)
) AS w(kolejnosc, krok, kwota)
ORDER BY kolejnosc;


-- KARTA 1.5 [Wykres skumulowany] — Struktura kosztów miesiąc po miesiącu
-- Wizualizacja: Stacked bar; oś X: Okres, seria: Kategoria
SELECT
    period_label AS "Okres",
    kategoria    AS "Kategoria kosztu",
    SUM(kwota)   AS "Kwota"
FROM mart.mart_cost_structure
GROUP BY period_label, period_start, kategoria, kolejnosc
ORDER BY period_start, kolejnosc;


-- KARTA 1.6 [Tabela] — Rozliczenie w rozbiciu na kanały
-- Wizualizacja: Table z formatowaniem walutowym
SELECT
    period_label            AS "Okres",
    channel_name            AS "Kanał",
    payer_name              AS "Płatnik",
    fv_partnera_netto       AS "FV partnera",
    kfv_netto               AS "Korekty / zwroty",
    saldo_towaru            AS "Saldo towaru",
    fv_uslugowa             AS "FV usługowa",
    do_zaplaty              AS "DO ZAPŁATY",
    zarobek_partnera        AS "Zarobek",
    wskaznik_zwrotow        AS "Zwroty %",
    marza_po_kosztach_pct   AS "Marża po kosztach"
FROM mart.mart_settlement_monthly
ORDER BY period_start DESC, channel_code;


-- KARTA 1.7 [Liczba, alert] — Kontrole poprawności rozliczenia
-- Wizualizacja: Number. Ustaw alert w Metabase: wyślij mail gdy wartość > 0.
SELECT COUNT(*) AS "Rozliczenia z niezgodną arytmetyką"
FROM mart.mart_settlement_monthly
WHERE NOT (kontrola_saldo_ok AND kontrola_do_zaplaty_ok AND kontrola_prowizji_ok);


-- KARTA 1.8 [Wykres liniowy] — Dynamika miesiąc do miesiąca
SELECT
    period_label               AS "Okres",
    do_zaplaty_zmiana_pct      AS "Zmiana kwoty do zapłaty",
    zarobek_zmiana_pct         AS "Zmiana zarobku",
    obrot_zmiana_pct           AS "Zmiana obrotu"
FROM mart.mart_settlement_monthly
WHERE channel_code = 'MAIN'
ORDER BY period_start;
