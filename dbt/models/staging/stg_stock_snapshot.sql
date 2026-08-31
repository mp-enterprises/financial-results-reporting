{{ config(materialized='view') }}

-- Zdjęcie magazynu na koniec okresu. Obejmuje WSZYSTKIE kanały łącznie
-- (towar leży na jednym magazynie, niezależnie gdzie się sprzedaje).

select
    st.partner_id,
    p.partner_code,
    st.period_id,
    per.period_label,
    per.period_start,
    st.product_id,
    pr.index_code,
    coalesce(pr.product_name, pr.index_code) as product_name,
    st.qty_on_hand,
    st.purchase_value,
    case when st.qty_on_hand > 0
         then st.purchase_value / st.qty_on_hand end as cena_zakupu_szt,
    st.sales_month,
    st.sales_3m,
    st.sales_total,
    st.avg_daily,
    st.days_cover,
    st.days_cover_capped,
    st.stock_status,

    -- kategoria ryzyka niezależna od progów nadawcy — pozwala porównywać
    -- okresy nawet jeśli operator zmieni swoje progi
    case
        when st.sales_3m = 0 and st.qty_on_hand > 0 then 'martwy'
        when st.days_cover is null                  then 'nieokreslony'
        when st.days_cover <= 30                    then 'pilne'
        when st.days_cover <= 60                    then 'do_dozamowienia'
        when st.days_cover <= 180                   then 'zdrowy'
        when st.days_cover <= 365                   then 'wolno_rotujacy'
        else 'nadmiarowy'
    end as kategoria_rotacji,

    -- kapitał zamrożony: wartość zapasu ponad 90-dniowe zapotrzebowanie
    case
        when st.avg_daily is null or st.avg_daily = 0 then st.purchase_value
        when st.qty_on_hand > st.avg_daily * 90
            then (st.qty_on_hand - st.avg_daily * 90)
                 * (st.purchase_value / nullif(st.qty_on_hand, 0))
        else 0
    end as kapital_nadmiarowy

from {{ source('core', 'stock_snapshot') }} st
join {{ source('core', 'partner') }} p   on p.partner_id = st.partner_id
join {{ source('core', 'period') }}  per on per.period_id = st.period_id
join {{ source('core', 'product') }} pr  on pr.product_id = st.product_id
