{{ config(materialized='table') }}

-- ZIARNO: partner × okres × kanał.
-- Podstawowa tabela finansowa: co przyszło, co odeszło, ile zostało.

with base as (
    select * from {{ ref('stg_settlement') }}
),

z_trendem as (
    select
        b.*,
        lag(b.do_zaplaty)      over w as do_zaplaty_poprz,
        lag(b.zarobek_partnera) over w as zarobek_poprz,
        lag(b.koszty_razem)    over w as koszty_poprz,
        lag(b.saldo_towaru)    over w as saldo_poprz
    from base b
    window w as (partition by b.partner_id, b.channel_code order by b.period_start)
)

select
    settlement_id,
    partner_id,
    partner_code,
    period_id,
    period_label,
    period_start,
    period_year,
    period_month,
    channel_code,
    channel_name,
    payer_name,
    revision,

    -- ---- pieniądze wpływające do partnera ----
    fv_partnera_netto,
    kfv_netto,
    saldo_towaru,
    fv_uslugowa,
    do_zaplaty,

    -- ---- rachunek zysku ----
    zysk_ze_sprzedazy,
    koszty_ogolne,
    koszty_indywidualne,
    koszty_platformy,
    koszty_transport,
    koszty_indywidualne_pozostale,
    koszty_razem,
    zysk_po_kosztach,
    stawka_prowizji,
    prowizja_operatora,
    zarobek_partnera,

    -- zwrot kosztu towaru = różnica między przelewem a faktycznym zarobkiem
    do_zaplaty - zarobek_partnera as zwrot_kosztu_towaru,

    -- ---- wolumen ----
    fv_partnera_szt,
    kfv_szt,
    sztuk_netto,
    srednia_cena_szt,

    -- ---- wskaźniki ----
    wskaznik_zwrotow,
    marza_po_kosztach_pct,
    udzial_uslugi_w_saldzie,
    zarobek_do_obrotu,
    case when koszty_razem > 0 then koszty_platformy / koszty_razem end as udzial_platform_w_kosztach,
    case when koszty_razem > 0 then koszty_transport / koszty_razem end as udzial_transportu_w_kosztach,
    case when zysk_ze_sprzedazy > 0 then koszty_razem / zysk_ze_sprzedazy end as koszty_do_zysku,

    -- ---- dynamika miesiąc do miesiąca ----
    do_zaplaty_poprz,
    zarobek_poprz,
    do_zaplaty - do_zaplaty_poprz as do_zaplaty_zmiana,
    zarobek_partnera - zarobek_poprz as zarobek_zmiana,
    case when do_zaplaty_poprz > 0
         then (do_zaplaty - do_zaplaty_poprz) / do_zaplaty_poprz end as do_zaplaty_zmiana_pct,
    case when zarobek_poprz > 0
         then (zarobek_partnera - zarobek_poprz) / zarobek_poprz end as zarobek_zmiana_pct,
    case when saldo_poprz > 0
         then (saldo_towaru - saldo_poprz) / saldo_poprz end as obrot_zmiana_pct,

    -- ---- magazyn (tylko kanał główny) ----
    magazyn_wartosc,
    magazyn_szt,
    magazyn_sku,

    -- ---- rekoncyliacja z arkuszem Raport ----
    raport_total_wartosc,
    raport_total_zysk,
    raport_total_wartosc - saldo_towaru   as roznica_raport_vs_karta,
    raport_total_zysk - zysk_ze_sprzedazy as roznica_zysku_raport_vs_karta,

    -- ---- kontrole arytmetyki (TRUE = zgodne) ----
    abs(coalesce(fv_partnera_netto,0) - coalesce(kfv_netto,0) - coalesce(saldo_towaru,0)) < 0.01
        as kontrola_saldo_ok,
    abs(coalesce(saldo_towaru,0) - coalesce(fv_uslugowa,0) - coalesce(do_zaplaty,0)) < 0.01
        as kontrola_do_zaplaty_ok,
    abs(greatest(coalesce(zysk_po_kosztach,0) * coalesce(stawka_prowizji,0), 0)
        - coalesce(prowizja_operatora,0)) < 0.01
        as kontrola_prowizji_ok

from z_trendem
