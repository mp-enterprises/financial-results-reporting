.PHONY: up down logs migrate ingest status test dbt backup restore

up:            ## uruchom cały stack
	docker compose up -d --build

down:          ## zatrzymaj stack
	docker compose down

logs:          ## logi workera
	docker compose logs -f ingest

migrate:       ## zastosuj migracje SQL
	docker compose exec ingest python -m app.cli migrate

ingest:        ## wgraj plik: make ingest PLIK=/data/archive/x.xlsx
	docker compose exec ingest python -m app.cli ingest $(PLIK)

status:        ## stan systemu
	docker compose exec ingest python -m app.cli status

test:          ## testy parsera
	docker compose exec ingest python -m pytest tests -q

dbt:           ## przelicz modele analityczne
	docker compose exec ingest dbt build --project-dir /app/dbt --profiles-dir /app/dbt

backup:        ## kopia zapasowa na żądanie
	docker compose exec postgres pg_dump -U settlements settlements | gzip > kopia_$$(date +%Y%m%d_%H%M).sql.gz

restore:       ## odtworzenie: make restore PLIK=kopia.sql.gz
	gunzip -c $(PLIK) | docker compose exec -T postgres psql -U settlements -d settlements
