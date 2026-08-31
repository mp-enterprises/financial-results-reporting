{{ config(materialized='view') }}

-- Sprzedaż po korektach w rozbiciu na SKU (tylko kanał główny).

select
    sl.settlement_id,
    s.partner_id,
    s.partner_code,
    s.period_id,
    s.period_label,
    s.period_start,
    s.channel_code,
    sl.product_id,
    pr.index_code,
    coalesce(pr.product_name, pr.index_code)                       as product_name,
    pr.is_asin_like,
    sl.quantity,
    sl.unit_price,
    sl.net_value,
    sl.profit,
    sl.net_value - sl.profit                                       as koszt_wlasny,
    case when sl.net_value <> 0 then sl.profit / sl.net_value end  as marza_pct,
    case when sl.quantity <> 0 then sl.profit / sl.quantity end    as zysk_na_sztuke,
    sl.profit < 0                                                  as sprzedane_ze_strata,
    sl.line_no

from {{ source('core', 'sales_line') }} sl
join {{ ref('stg_settlement') }} s on s.settlement_id = sl.settlement_id
join {{ source('core', 'product') }} pr on pr.product_id = sl.product_id
