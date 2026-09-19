-- Test pojedynczy: koszty administracyjne rozdzielone między partnerów muszą
-- w każdym okresie dawać dokładnie pełną miesięczną kwotę (nic nie zginęło,
-- nic nie zostało policzone dwa razy).

select
    period_id,
    sum(koszty_stale)              as rozdzielone,
    max(koszty_stale_pelne_mies)   as pelna_kwota,
    abs(sum(koszty_stale) - max(koszty_stale_pelne_mies)) as roznica
from {{ ref('mart_rentownosc_kapitalu') }}
group by 1
having abs(sum(koszty_stale) - max(koszty_stale_pelne_mies)) > 0.01
