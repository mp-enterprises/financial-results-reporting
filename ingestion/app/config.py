"""Konfiguracja z zmiennych środowiskowych."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://settlements:settlements@localhost:5432/settlements"
    )
    archive_dir: Path = Path(os.getenv("ARCHIVE_DIR", "/data/archive"))
    api_key: str = os.getenv("INGEST_API_KEY", "")
    dbt_project_dir: Path = Path(os.getenv("DBT_PROJECT_DIR", "/app/dbt"))
    dbt_profiles_dir: Path = Path(os.getenv("DBT_PROFILES_DIR", "/app/dbt"))
    run_dbt_after_load: bool = os.getenv("RUN_DBT_AFTER_LOAD", "true").lower() == "true"
    dbt_timeout_s: int = int(os.getenv("DBT_TIMEOUT_S", "900"))
    # tolerancja rekoncyliacji w PLN — poniżej tej wartości różnica to zaokrąglenie
    reconciliation_tolerance: float = float(os.getenv("RECON_TOLERANCE", "0.01"))
    admin_user: str = os.getenv("ADMIN_USER", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "50"))


settings = Settings()
