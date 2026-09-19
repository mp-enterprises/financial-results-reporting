{{ config(materialized='table') }}

-- ZIARNO: partner × okres.
-- Rentowność zainwestowanego kapitału (ROIC) z uwzględnieniem:
--   1) stałych kosztów administracyjnych spółki (księgowość + koszty roczne / 12),
--   2) czasu zamrożenia kapitału (towar płacimy przed sprzedażą),
--   3) kosztu tego kapitału (stopa dyskontowa).
--
-- To jest JEDYNE miejsce z tą logiką. Model bazuje na mart_partner_pnl i niczego
-- w istniejących modelach nie zmienia — wszystkie założenia są parametrami
-- poniżej (nadpisywane przez `--vars` albo `vars:` w dbt_project.yml).
--
-- LOGIKA
--   koszt_towaru      = obrot_netto − zysk_ze_sprzedazy (oba kanały; ten sam wzór co
--                       w rotacja_magazynu_rocznie). To kapitał, który „wrócił”
--                       sprzedażą w tym miesiącu.
--   L (czas zamrożenia) = opóźnienie dostawy + okno sprzedaży / 2.
--                       Zamówienie → sprzedaż po ~2 mies., potem rozprzedaje się ~6 mies.,
--                       więc przeciętna sztuka czeka na sprzedaż 2 + 3 = 5 mies.
--   kapital_w_obiegu  = koszt_towaru × L. Przy stałej sprzedaży (prawo Little’a) tyle
--                       kapitału trzeba mieć „w drodze i na półce”, żeby sprzedawać ten
--                       wolumen. To właściwy mianownik — nie koszt towaru z jednego
--                       miesiąca, bo ten pomija czas, przez który pieniądze leżały.
--   koszty_stale      = koszty administracyjne miesiąca × udział partnera w koszcie
--                       towaru (przy jednym partnerze = 100%). Odejmowane w miesiącu,
--                       w którym powstały; opóźnienie zakupu jest już w mianowniku,
--                       więc przesuwanie ich dodatkowo liczyłoby ten sam czas dwa razy.
--   roic_roczny       = (zarobek_partnera − koszty_stale) × 12 / kapital_w_obiegu
--                       = zwrot za jeden cykl × 12 / L. Prosta annualizacja
--                       (bez procentu składanego), żeby nie zawyżać wyniku.
--   koszt_kapitalu    = koszt_towaru × ((1 + r)^(L/12) − 1); zysk_ekonomiczny to zysk
--                       po kosztach stałych pomniejszony o ten koszt.
--
-- ZAŁOŻENIA I OGRANICZENIA
--   - Zysk netto = zarobek_partnera: po prowizji operatora, PRZED podatkiem dochodowym,
--     w PLN netto (bez VAT).
--   - L jest stałą z parametrów, nie pomiarem. Kolumny *_wg_magazynu to kontrola:
--     magazyn_wartosc istnieje dopiero od 2026-M07 i nie zawiera towaru w drodze, więc
--     rzeczywisty kapitał jest większy niż sam magazyn.
--   - Okna kroczące liczone po wierszach (jak zarobek_srednia_3m w mart_partner_pnl);
--     przy brakującym miesiącu obejmują starszy okres — sprawdź `miesiecy_w_oknie_3m`.

{% set ksiegowosc_mies       = var('rk_ksiegowosc_miesiecznie', 749) %}
{% set koszty_roczne_inne    = var('rk_koszty_roczne_inne', 10000) %}
{% set opoznienie_dostawy    = var('rk_opoznienie_dostawy_mies', 2) %}
{% set okno_sprzedazy        = var('rk_okno_sprzedazy_mies', 6) %}
{% set stopa_dyskontowa      = var('rk_stopa_dyskontowa_rocznie', 0.12) %}

with zalozenia as (
    select
        {{ ksiegowosc_mies }}::numeric                                   as ksiegowosc_mies,
        {{ koszty_roczne_inne }}::numeric                                as koszty_roczne_inne,
        {{ ksiegowosc_mies }}::numeric + {{ koszty_roczne_inne }}::numeric / 12
                                                                         as koszty_stale_pelne_mies,
        {{ opoznienie_dostawy }}::numeric + {{ okno_sprzedazy }}::numeric / 2
                                                                         as czas_zamrozenia_mies,
        {{ stopa_dyskontowa }}::numeric                                  as stopa_dyskontowa_rocznie
),

baza as (
    select
        p.partner_id,
        p.partner_code,
        p.period_id,
        p.period_label,
        p.period_start,
        p.period_year,
        p.period_month,
        p.obrot_netto,
        p.zysk_ze_sprzedazy,
        p.zarobek_partnera,
        nullif(p.magazyn_wartosc, 0)                                     as magazyn_wartosc,
        p.obrot_netto - p.zysk_ze_sprzedazy                              as koszt_towaru
    from {{ ref('mart_partner_pnl') }} p
),

z_udzialem as (
    -- udział w kosztach stałych: proporcjonalnie do kosztu towaru partnera w okresie;
    -- gdy nikt nie ma dodatniego kosztu towaru, dzielimy po równo — suma = 100%
    select
        b.*,
        case
            when sum(greatest(b.koszt_towaru, 0)) over (partition by b.period_id) > 0
                then greatest(b.koszt_towaru, 0)
                     / sum(greatest(b.koszt_towaru, 0)) over (partition by b.period_id)
            else 1.0 / count(*) over (partition by b.period_id)
        end as udzial_kosztow_stalych
    from baza b
),

wyliczone as (
    select
        u.*,
        z.czas_zamrozenia_mies,
        z.stopa_dyskontowa_rocznie,
        z.koszty_stale_pelne_mies,

        u.koszt_towaru * z.czas_zamrozenia_mies                          as kapital_w_obiegu,
        z.koszty_stale_pelne_mies * u.udzial_kosztow_stalych             as koszty_stale,
        u.zarobek_partnera
            - z.koszty_stale_pelne_mies * u.udzial_kosztow_stalych       as zysk_po_kosztach_stalych,

        -- koszt zatrudnionego kapitału za czas jego zamrożenia (odsetki składane)
        u.koszt_towaru
            * (power(1 + z.stopa_dyskontowa_rocznie, z.czas_zamrozenia_mies / 12) - 1)
                                                                         as koszt_kapitalu
    from z_udzialem u
    cross join zalozenia z
),

z_metrykami as (
    select
        w.*,
        w.zysk_po_kosztach_stalych - w.koszt_kapitalu                    as zysk_ekonomiczny,

        -- naiwna miara dla porównania: zysk / koszt towaru z tego samego miesiąca
        case when w.koszt_towaru > 0
             then w.zarobek_partnera / w.koszt_towaru end                as zwrot_naiwny_miesiac,

        -- zwrot za jeden cykl obrotu kapitału (L miesięcy)
        case when w.koszt_towaru > 0
             then w.zysk_po_kosztach_stalych / w.koszt_towaru end        as zwrot_za_cykl,

        -- GŁÓWNA METRYKA: roczna stopa zwrotu z zainwestowanego kapitału
        case when w.kapital_w_obiegu > 0
             then w.zysk_po_kosztach_stalych * 12 / w.kapital_w_obiegu end as roic_roczny,

        -- koszt kapitału w tej samej konwencji (prosta annualizacja) i zwrot ponad niego
        case when w.kapital_w_obiegu > 0
             then w.koszt_kapitalu * 12 / w.kapital_w_obiegu end         as koszt_kapitalu_roczny,
        case when w.kapital_w_obiegu > 0
             then (w.zysk_po_kosztach_stalych - w.koszt_kapitalu) * 12
                  / w.kapital_w_obiegu end                               as roic_ekonomiczny_roczny,

        -- kontrola względem rzeczywistego magazynu (tylko okresy z arkuszem Stok)
        case when w.koszt_towaru > 0 and w.magazyn_wartosc is not null
             then w.magazyn_wartosc / w.koszt_towaru end                 as magazyn_w_miesiacach_kosztu,
        case when w.magazyn_wartosc is not null
             then w.zysk_po_kosztach_stalych * 12 / w.magazyn_wartosc end as roic_roczny_wg_magazynu,

        -- okno kroczące 3M: stosunek sum, nie średnia ze stosunków
        count(*) over w3                                                 as miesiecy_w_oknie_3m,
        case when sum(w.koszt_towaru) over w3 > 0
             then sum(w.zysk_po_kosztach_stalych) over w3
                  / sum(w.koszt_towaru) over w3
                  * 12 / w.czas_zamrozenia_mies end                      as roic_roczny_3m,
        case when sum(w.koszt_towaru) over w3 > 0
             then sum(w.zysk_po_kosztach_stalych - w.koszt_kapitalu) over w3
                  / sum(w.koszt_towaru) over w3
                  * 12 / w.czas_zamrozenia_mies end                      as roic_ekonomiczny_roczny_3m
    from wyliczone w
    window w3 as (partition by w.partner_id order by w.period_start
                  rows between 2 preceding and current row)
)

select
    partner_id,
    partner_code,
    period_id,
    period_label,
    period_start,
    period_year,
    period_month,

    -- ---- wejście z istniejących modeli ----
    obrot_netto,
    zysk_ze_sprzedazy,
    koszt_towaru,
    zarobek_partnera                as zysk_netto,

    -- ---- założenia (w każdym wierszu, żeby dashboard był samoopisujący) ----
    czas_zamrozenia_mies,
    stopa_dyskontowa_rocznie,
    koszty_stale_pelne_mies,
    udzial_kosztow_stalych,

    -- ---- rachunek kapitału ----
    kapital_w_obiegu,
    koszty_stale,
    zysk_po_kosztach_stalych,
    koszt_kapitalu,
    zysk_ekonomiczny,

    -- ---- metryki ----
    roic_roczny,
    koszt_kapitalu_roczny,
    roic_ekonomiczny_roczny,
    zwrot_za_cykl,
    roic_roczny_3m,
    roic_ekonomiczny_roczny_3m,
    miesiecy_w_oknie_3m,

    -- ---- porównania i kontrola ----
    zwrot_naiwny_miesiac,
    magazyn_wartosc,
    magazyn_w_miesiacach_kosztu,
    roic_roczny_wg_magazynu

from z_metrykami
