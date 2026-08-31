-- Test pojedynczy: konsolidacja P&L musi się zgadzać z sumą kanałów.
-- Jeśli ten test padnie, agregacja w mart_partner_pnl rozjechała się
-- z danymi źródłowymi — nie wolno publikować takich liczb.

with kanaly as (
    select partner_id, period_id,
           sum(do_zaplaty)      as do_zaplaty,
           sum(zarobek_partnera) as zarobek
    from {{ ref('mart_settlement_monthly') }}
    group by 1, 2
),

skonsolidowane as (
    select partner_id, period_id, do_zaplaty_razem, zarobek_partnera
    from {{ ref('mart_partner_pnl') }}
)

select
    k.partner_id,
    k.period_id,
    k.do_zaplaty as suma_kanalow,
    s.do_zaplaty_razem as w_pnl,
    abs(k.do_zaplaty - s.do_zaplaty_razem) as roznica
from kanaly k
join skonsolidowane s using (partner_id, period_id)
where abs(k.do_zaplaty - s.do_zaplaty_razem) > 0.01
   or abs(k.zarobek - s.zarobek_partnera)    > 0.01
