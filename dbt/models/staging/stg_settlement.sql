{{ config(materialized='view') }}

-- Aktualne (nieskorygowane) rozliczenia z czytelnymi wymiarami.
-- Wszystkie modele analityczne budują na tym widoku, więc korekta pliku
-- automatycznie przechodzi przez całą warstwę raportową.

select
    s.settlement_id,
    s.partner_id,
    p.partner_code,
    s.period_id,
    per.period_year,
    per.period_month,
    per.period_label,
    per.period_start,
    per.period_end,
    s.channel_code,
    ch.channel_name,
    ch.payer_name,
    s.revision,
    s.file_id,

    -- koszty
    s.koszty_ogolne,
    s.koszty_indywidualne,
    coalesce(s.koszty_platformy, 0)                            as koszty_platformy,
    coalesce(s.koszty_transport, 0)                            as koszty_transport,
    greatest(
        s.koszty_indywidualne - coalesce(s.koszty_platformy, 0) - coalesce(s.koszty_transport, 0),
        0
    )                                                          as koszty_indywidualne_pozostale,
    s.koszty_ogolne + s.koszty_indywidualne                    as koszty_razem,

    -- rachunek wyniku
    s.zysk_ze_sprzedazy,
    s.zysk_po_kosztach,
    s.stawka_prowizji,
    s.prowizja_operatora,
    s.zysk_po_kosztach - coalesce(s.prowizja_operatora, 0)     as zarobek_partnera,

    -- faktura usługowa
    s.fv_bx,
    s.usluga_razem,
    s.zwroty_wartosciowe,
    s.fv_uslugowa,

    -- strona towarowa
    s.fv_partnera_netto,
    s.fv_partnera_szt,
    s.kfv_netto,
    s.kfv_szt,
    s.saldo_towaru,
    s.srednia_cena_szt,
    s.do_zaplaty,
    coalesce(s.fv_partnera_szt, 0) - coalesce(s.kfv_szt, 0)    as sztuk_netto,

    -- wskaźniki
    case when s.fv_partnera_netto > 0
         then s.kfv_netto / s.fv_partnera_netto end            as wskaznik_zwrotow,
    case when s.zysk_ze_sprzedazy > 0
         then s.zysk_po_kosztach / s.zysk_ze_sprzedazy end     as marza_po_kosztach_pct,
    case when s.saldo_towaru > 0
         then s.fv_uslugowa / s.saldo_towaru end               as udzial_uslugi_w_saldzie,
    case when s.saldo_towaru > 0
         then (s.zysk_po_kosztach - coalesce(s.prowizja_operatora, 0)) / s.saldo_towaru end
                                                               as zarobek_do_obrotu,

    -- przelicznik ceny
    s.prowizja_marketplace_pct,
    s.prowizja_techniczna_pct,
    s.przelicznik_ceny_fv,

    -- magazyn
    s.magazyn_wartosc,
    s.magazyn_szt,
    s.magazyn_sku,

    -- sumy kontrolne z arkusza Raport
    s.raport_total_szt,
    s.raport_total_wartosc,
    s.raport_total_zysk,

    s.created_at

from {{ source('core', 'settlement') }} s
join {{ source('core', 'partner') }} p   on p.partner_id = s.partner_id
join {{ source('core', 'period') }}  per on per.period_id = s.period_id
join {{ source('core', 'channel') }} ch  on ch.channel_code = s.channel_code
where s.is_current
