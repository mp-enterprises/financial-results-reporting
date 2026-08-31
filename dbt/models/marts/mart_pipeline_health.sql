{{ config(materialized='table') }}

-- Zdrowie procesu: czy plik za każdy miesiąc dotarł, czy się załadował,
-- czy kwoty się spięły. To jest tabela pod alert „brak rozliczenia za lipiec”.

with pliki as (
    select
        f.file_id, f.file_name, f.file_sha256, f.partner_code,
        f.period_year, f.period_month, f.source, f.status,
        f.received_at, f.processed_at, f.generated_at, f.error_message
    from {{ source('raw', 'ingested_file') }} f
),

kontrole as (
    select
        s.file_id,
        count(*)                                                    as kontroli,
        count(*) filter (where not c.passed and c.severity = 'error')   as bledow,
        count(*) filter (where not c.passed and c.severity = 'warning') as ostrzezen,
        max(abs(c.difference)) filter (where not c.passed)          as max_roznica
    from {{ source('ops', 'data_quality_check') }} c
    join {{ source('core', 'settlement') }} s on s.settlement_id = c.settlement_id
    group by 1
),

uruchomienia as (
    select
        file_id,
        max(started_at)                     as ostatni_start,
        max(finished_at)                    as ostatni_koniec,
        (array_agg(status order by started_at desc))[1]     as ostatni_status,
        (array_agg(dbt_status order by started_at desc))[1] as ostatni_dbt,
        count(*)                            as prob
    from {{ source('ops', 'pipeline_run') }}
    group by 1
)

select
    p.file_id,
    p.file_name,
    left(p.file_sha256, 12)                as sha_skrot,
    p.partner_code,
    p.period_year,
    p.period_month,
    to_char(make_date(p.period_year, p.period_month, 1), 'YYYY-MM') as period_label,
    p.source                               as zrodlo,
    p.status                               as status_pliku,
    p.received_at                          as odebrano,
    p.processed_at                         as przetworzono,
    p.generated_at                         as wygenerowano_przez_nadawce,
    extract(epoch from (p.processed_at - p.received_at))  as czas_przetwarzania_s,
    p.error_message                        as blad,
    coalesce(k.kontroli, 0)                as kontroli,
    coalesce(k.bledow, 0)                  as kontroli_nieudanych,
    coalesce(k.ostrzezen, 0)               as ostrzezen,
    k.max_roznica,
    u.ostatni_status,
    u.ostatni_dbt,
    u.prob                                 as liczba_prob,

    -- status jest typem ENUM, więc porównania i wynik rzutujemy na tekst
    case
        when p.status in ('failed', 'quarantined')     then 'BŁĄD — plik nie wszedł do bazy'
        when coalesce(k.bledow, 0) > 0                 then 'UWAGA — kwoty się nie spinają'
        when p.status = 'loaded'                       then 'Załadowany (bez transformacji)'
        when p.status = 'transformed'                  then 'OK'
        else p.status::text
    end as ocena

from pliki p
left join kontrole k     on k.file_id = p.file_id
left join uruchomienia u on u.file_id = p.file_id
