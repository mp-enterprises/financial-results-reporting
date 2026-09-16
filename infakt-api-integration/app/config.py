"""Konfiguracja z zmiennych środowiskowych.

Wzorowana na ingestion/app/config.py: proste dataclass'y czytające os.getenv,
bez plików konfiguracyjnych. Wartości domyślne są ustalane raz, w chwili
importu modułu (standardowa charakterystyka dataclass'ów) — to wystarcza dla
procesu CLI uruchamianego raz na wywołanie, ale w testach wymaga
importlib.reload() po zmianie zmiennych środowiskowych (patrz tests/test_config.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # klucz z zakresami api:invoices:read, api:costs:read, api:accounting:read
    api_key: str = os.getenv("INFAKT_API_KEY", "")
    # "sandbox" (https://api.sandbox-infakt.pl) albo "production" (https://api.infakt.pl)
    environment: str = os.getenv("INFAKT_ENV", "sandbox")
    output_dir: Path = Path(os.getenv("INFAKT_OUTPUT_DIR", "./data"))
    max_retries: int = int(os.getenv("INFAKT_MAX_RETRIES", "5"))
    timeout_s: float = float(os.getenv("INFAKT_TIMEOUT_S", "30"))
    # lista NIP-ów dostawców do wykluczenia z kosztów, rozdzielona przecinkami
    supplier_denylist_nip: str = os.getenv("INFAKT_SUPPLIER_DENYLIST_NIP", "")


settings = Settings()
