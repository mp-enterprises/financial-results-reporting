{{ config(materialized='table') }}

-- ZIARNO: partner × okres × kanał × kategoria kosztu.
-- Format „długi” — wprost pod wykresy słupkowe skumulowane w Metabase.

with base as (
    select * from {{ ref('mart_settlement_monthly') }}
),

unpivoted as (
    select partner_id, partner_code, period_id, period_label, period_start, channel_code,
           1 as kolejnosc, 'Prowizje platform sprzedażowych' as kategoria, koszty_platformy as kwota,
           saldo_towaru, zysk_ze_sprzedazy
    from base
    union all
    select partner_id, partner_code, period_id, period_label, period_start, channel_code,
           2, 'Transport', koszty_transport, saldo_towaru, zysk_ze_sprzedazy from base
    union all
    select partner_id, partner_code, period_id, period_label, period_start, channel_code,
           3, 'Pozostałe koszty indywidualne', koszty_indywidualne_pozostale, saldo_towaru, zysk_ze_sprzedazy
    from base
    union all
    select partner_id, partner_code, period_id, period_label, period_start, channel_code,
           4, 'Koszty ogólne (udział wspólny)', koszty_ogolne, saldo_towaru, zysk_ze_sprzedazy from base
    union all
    select partner_id, partner_code, period_id, period_label, period_start, channel_code,
           5, 'Wynagrodzenie operatora (prowizja)', prowizja_operatora, saldo_towaru, zysk_ze_sprzedazy
    from base
)

select
    partner_id,
    partner_code,
    period_id,
    period_label,
    period_start,
    channel_code,
    kolejnosc,
    kategoria,
    coalesce(kwota, 0) as kwota,
    case when zysk_ze_sprzedazy > 0 then coalesce(kwota, 0) / zysk_ze_sprzedazy end as udzial_w_zysku_brutto,
    case when saldo_towaru > 0     then coalesce(kwota, 0) / saldo_towaru end       as udzial_w_obrocie,
    sum(coalesce(kwota, 0)) over (partition by partner_id, period_id, channel_code) as koszty_okresu_razem
from unpivoted
where coalesce(kwota, 0) <> 0
