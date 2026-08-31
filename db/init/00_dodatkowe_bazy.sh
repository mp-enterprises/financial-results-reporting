#!/bin/bash
# Tworzy osobne bazy dla Metabase i n8n, żeby ich metadane nie mieszały się
# z danymi rozliczeniowymi. Uruchamiane raz, przy pierwszej inicjalizacji.
set -e
for db in "${METABASE_DB:-metabase}" "${N8N_DB:-n8n}"; do
  echo "Tworzę bazę $db"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
      SELECT 'CREATE DATABASE "$db" OWNER "$POSTGRES_USER"'
      WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
SQL
done
