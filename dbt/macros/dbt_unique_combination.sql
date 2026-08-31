{#
    Własny odpowiednik dbt_utils.unique_combination_of_columns.
    Trzymamy go u siebie, żeby projekt nie wymagał `dbt deps` ani dostępu
    do internetu przy wdrożeniu.
#}
{% test dbt_unique_combination(model, columns) %}

with liczone as (
    select
        {{ columns | join(', ') }},
        count(*) as liczba_wierszy
    from {{ model }}
    group by {{ range(1, columns | length + 1) | join(', ') }}
    having count(*) > 1
)
select * from liczone

{% endtest %}
