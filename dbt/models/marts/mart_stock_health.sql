{{ config(materialized='table') }}

-- ZIARNO: produkt × okres. Zdrowie magazynu i kapitał zamrożony.
-- Łączy zdjęcie stanu z rentownością sprzedaży tego samego SKU, żeby
-- odpowiedzieć nie tylko "co leży", ale "czy warto to w ogóle trzymać".

with stok as (
    select * from {{ ref('stg_stock_snapshot') }}
),

sprzedaz as (
    select
        partner_id, period_id, product_id,
        net_value, profit, marza_pct, quantity
    from {{ ref('stg_sales_line') }}
)

select
    st.partner_id,
    st.partner_code,
    st.period_id,
    st.period_label,
    st.period_start,
    st.product_id,
    st.index_code,
    st.product_name,

    st.qty_on_hand                as stan_szt,
    st.purchase_value             as wartosc_zakupu,
    st.cena_zakupu_szt,
    st.sales_month                as sprzedaz_miesiac,
    st.sales_3m                   as sprzedaz_3m,
    st.sales_total                as sprzedaz_lacznie,
    st.avg_daily                  as srednia_dzienna,
    st.days_cover                 as wystarczy_na_dni,
    st.days_cover_capped,
    st.stock_status               as status_nadawcy,
    st.kategoria_rotacji,
    st.kapital_nadmiarowy,

    -- rentowność tego SKU w tym samym okresie (kanał główny)
    sp.net_value                  as sprzedaz_wartosc,
    sp.profit                     as sprzedaz_zysk,
    sp.marza_pct                  as sprzedaz_marza_pct,

    -- ile miesięcy zajmie wyprzedanie stanu przy obecnym tempie
    case when st.avg_daily > 0 then st.qty_on_hand / (st.avg_daily * 30.44) end as miesiecy_zapasu,

    -- ile zysku "wisi" jeszcze w tym stanie przy obecnej marży
    case when sp.marza_pct is not null and st.cena_zakupu_szt is not null and sp.marza_pct < 1
         then st.qty_on_hand * st.cena_zakupu_szt * (sp.marza_pct / nullif(1 - sp.marza_pct, 0))
    end as potencjalny_zysk_ze_stanu,

    -- rekomendacja operacyjna
    case
        when st.sales_3m = 0 and st.qty_on_hand > 0 and st.purchase_value >= 500
            then 'wyprzedaz / likwidacja — kapitał zamrożony'
        when st.sales_3m = 0 and st.qty_on_hand > 0
            then 'martwy stok — do przeglądu'
        when st.days_cover is not null and st.days_cover <= 30
            then 'dozamówić pilnie'
        when st.days_cover is not null and st.days_cover <= 60
            then 'zaplanować dostawę'
        when st.days_cover is not null and st.days_cover > 365 and st.purchase_value >= 500
            then 'nadmiarowy zapas — wstrzymać zamówienia'
        when sp.profit < 0
            then 'sprzedaje się ze stratą — przejrzeć cenę'
        else 'bez działań'
    end as rekomendacja,

    -- flagi ułatwiające filtrowanie w Metabase
    (st.sales_3m = 0 and st.qty_on_hand > 0)                          as flaga_martwy,
    (st.days_cover is not null and st.days_cover <= 60)               as flaga_dozamowic,
    (st.purchase_value >= 1000 and st.sales_3m = 0)                   as flaga_duzy_martwy_kapital

from stok st
left join sprzedaz sp
       on sp.partner_id = st.partner_id
      and sp.period_id  = st.period_id
      and sp.product_id = st.product_id
