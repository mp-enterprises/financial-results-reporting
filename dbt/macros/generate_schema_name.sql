{# Bez tego dbt tworzyłby schematy typu "mart_mart".
   Chcemy dokładnie te nazwy, które podajemy w dbt_project.yml. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
