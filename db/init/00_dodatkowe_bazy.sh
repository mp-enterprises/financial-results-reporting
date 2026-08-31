#!/bin/bash
# Tworzy osobną bazę dla Metabase, żeby jego metadane nie mieszały się
# z danymi rozliczeniowymi. Uruchamiane raz, przy pierwszej inicjalizacji.
set -e
db="${METABASE_DB:-metabase}"
echo "Tworzę bazę $db"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    SELECT 'CREATE DATABASE "$db" OWNER "$POSTGRES_USER"'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
SQL
