"""Zapis wyników do plików JSON. Brak bazy danych — to celowe (patrz README.md)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def zapisz_json(dane: Any, sciezka: Path) -> Path:
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(json.dumps(dane, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return sciezka
