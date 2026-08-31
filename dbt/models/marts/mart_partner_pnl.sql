{{ config(materialized='table') }}

-- ZIARNO: partner × okres (oba kanały skonsolidowane).
-- To jest tabela "ile naprawdę zarobiliśmy w tym miesiącu".
-- UWAGA: kwoty DO ZAPŁATY z obu kanałów pochodzą od dwóch różnych podmiotów
-- i wpływają osobno — kolumny są rozbite, ale suma jest poprawna jako
-- łączny przypływ gotówki, nie jako jeden przelew.

with per_channel as (
    select * from {{ ref('mart_settlement_monthly') }}
),

agg as (
    select
        partner_id,
        partner_code,
        period_id,
        period_label,
        period_start,
        period_year,
        period_month,

        count(*)                                             as kanalow,
        sum(saldo_towaru)                                    as obrot_netto,
        sum(fv_partnera_netto)                               as fv_partnera_razem,
        sum(kfv_netto)                                       as zwroty_razem,
        sum(sztuk_netto)                                     as sztuk_netto,

        sum(zysk_ze_sprzedazy)                               as zysk_ze_sprzedazy,
        sum(koszty_ogolne)                                   as koszty_ogolne,
        sum(koszty_indywidualne)                             as koszty_indywidualne,
        sum(koszty_platformy)                                as koszty_platformy,
        sum(koszty_transport)                                as koszty_transport,
        sum(koszty_razem)                                    as koszty_razem,
        sum(zysk_po_kosztach)                                as zysk_po_kosztach,
        sum(prowizja_operatora)                              as prowizja_operatora,
        sum(zarobek_partnera)                                as zarobek_partnera,
        sum(fv_uslugowa)                                     as fv_uslugowa_razem,
        sum(do_zaplaty)                                      as do_zaplaty_razem,

        sum(do_zaplaty) filter (where channel_code = 'MAIN') as do_zaplaty_main,
        sum(do_zaplaty) filter (where channel_code = 'MJ')   as do_zaplaty_mj,
        sum(saldo_towaru) filter (where channel_code = 'MAIN') as obrot_main,
        sum(saldo_towaru) filter (where channel_code = 'MJ')   as obrot_mj,
        sum(zarobek_partnera) filter (where channel_code = 'MAIN') as zarobek_main,
        sum(zarobek_partnera) filter (where channel_code = 'MJ')   as zarobek_mj,

        max(magazyn_wartosc)                                 as magazyn_wartosc,
        max(magazyn_szt)                                     as magazyn_szt,
        max(magazyn_sku)                                     as magazyn_sku,

        bool_and(kontrola_saldo_ok and kontrola_do_zaplaty_ok and kontrola_prowizji_ok)
                                                             as kontrole_ok
    from per_channel
    group by 1, 2, 3, 4, 5, 6, 7
)

select
    a.*,
    a.do_zaplaty_razem - a.zarobek_partnera                as zwrot_kosztu_towaru,

    case when a.obrot_netto > 0 then a.zarobek_partnera / a.obrot_netto end     as rentownosc_netto,
    case when a.obrot_netto > 0 then a.zysk_ze_sprzedazy / a.obrot_netto end    as marza_brutto,
    case when a.obrot_netto > 0 then a.koszty_razem / a.obrot_netto end         as koszty_do_obrotu,
    case when a.fv_partnera_razem > 0 then a.zwroty_razem / a.fv_partnera_razem end as wskaznik_zwrotow,
    case when a.zysk_po_kosztach > 0 then a.prowizja_operatora / a.zysk_po_kosztach end as efektywna_stawka_prowizji,
    case when a.obrot_mj is not null and a.obrot_netto > 0
         then a.obrot_mj / a.obrot_netto end                                    as udzial_kanalu_mj,

    -- rotacja kapitału: ile razy w roku „obraca się” magazyn przy tym tempie
    case when a.magazyn_wartosc > 0
         then (a.obrot_netto - a.zysk_ze_sprzedazy) * 12 / a.magazyn_wartosc end as rotacja_magazynu_rocznie,
    case when a.magazyn_wartosc > 0
         then a.zarobek_partnera * 12 / a.magazyn_wartosc end                    as zwrot_z_kapitalu_rocznie,

    -- trend
    lag(a.zarobek_partnera)  over w as zarobek_poprz,
    lag(a.obrot_netto)       over w as obrot_poprz,
    avg(a.zarobek_partnera)  over (partition by a.partner_id order by a.period_start
                                   rows between 2 preceding and current row) as zarobek_srednia_3m,
    sum(a.zarobek_partnera)  over (partition by a.partner_id, a.period_year
                                   order by a.period_start) as zarobek_narastajaco_rok,
    sum(a.obrot_netto)       over (partition by a.partner_id, a.period_year
                                   order by a.period_start) as obrot_narastajaco_rok

from agg a
window w as (partition by a.partner_id order by a.period_start)
