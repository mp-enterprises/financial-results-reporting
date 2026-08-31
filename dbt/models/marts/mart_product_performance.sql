{{ config(
    materialized='table',
    indexes=[{'columns': ['partner_id', 'period_id'], 'type': 'btree'},
             {'columns': ['product_id'], 'type': 'btree'}]
) }}

-- ZIARNO: produkt × okres (kanał główny).
-- Odpowiada na pytania: co zarabia, co traci, co rośnie, co wypada z oferty.

with sprzedaz as (
    select * from {{ ref('stg_sales_line') }}
),

z_udzialem as (
    select
        s.*,
        sum(s.net_value) over (partition by s.settlement_id) as okres_wartosc,
        sum(s.profit)    over (partition by s.settlement_id) as okres_zysk,
        row_number() over (partition by s.settlement_id order by s.profit desc)    as ranking_zysk,
        row_number() over (partition by s.settlement_id order by s.net_value desc) as ranking_obrot
    from sprzedaz s
),

z_trendem as (
    select
        z.*,
        lag(z.profit)     over w as zysk_poprz,
        lag(z.net_value)  over w as wartosc_poprz,
        lag(z.quantity)   over w as ilosc_poprz,
        count(*)          over (partition by z.partner_id, z.product_id) as miesiecy_ze_sprzedaza
    from z_udzialem z
    window w as (partition by z.partner_id, z.product_id order by z.period_start)
)

select
    partner_id,
    partner_code,
    period_id,
    period_label,
    period_start,
    product_id,
    index_code,
    product_name,
    is_asin_like,

    quantity                                   as ilosc,
    unit_price                                 as cena_srednia,
    net_value                                  as wartosc_netto,
    profit                                     as zysk,
    koszt_wlasny,
    marza_pct,
    zysk_na_sztuke,
    sprzedane_ze_strata,

    -- udział w wyniku okresu
    case when okres_wartosc <> 0 then net_value / okres_wartosc end as udzial_w_obrocie,
    case when okres_zysk <> 0    then profit / okres_zysk end       as udzial_w_zysku,
    ranking_zysk,
    ranking_obrot,

    -- klasyfikacja ABC po zysku (A = 80% zysku okresu)
    case
        when sum(greatest(profit, 0)) over (partition by settlement_id order by profit desc
             rows unbounded preceding)
             <= 0.8 * sum(greatest(profit, 0)) over (partition by settlement_id) then 'A'
        when sum(greatest(profit, 0)) over (partition by settlement_id order by profit desc
             rows unbounded preceding)
             <= 0.95 * sum(greatest(profit, 0)) over (partition by settlement_id) then 'B'
        else 'C'
    end as klasa_abc,

    -- dynamika
    zysk_poprz,
    wartosc_poprz,
    ilosc_poprz,
    profit - zysk_poprz                        as zysk_zmiana,
    case when zysk_poprz > 0 then (profit - zysk_poprz) / zysk_poprz end as zysk_zmiana_pct,
    case when wartosc_poprz > 0 then (net_value - wartosc_poprz) / wartosc_poprz end as obrot_zmiana_pct,
    miesiecy_ze_sprzedaza,
    zysk_poprz is null                         as nowy_w_tym_okresie

from z_trendem
