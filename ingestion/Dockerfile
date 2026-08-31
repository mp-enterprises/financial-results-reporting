FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY ingestion/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ingestion/app /app/app
COPY db/migrations /app/db/migrations
COPY dbt /app/dbt

RUN mkdir -p /data/archive && useradd -m -u 10001 appuser && chown -R appuser /app /data
USER appuser

ENV DBT_PROJECT_DIR=/app/dbt DBT_PROFILES_DIR=/app/dbt ARCHIVE_DIR=/data/archive

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
