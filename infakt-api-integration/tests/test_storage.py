from __future__ import annotations

import json
from pathlib import Path

from app.storage import zapisz_json


def test_zapisz_json_tworzy_katalogi(tmp_path: Path):
    sciezka = tmp_path / "raw" / "2026-09" / "invoices.json"
    zapisz_json([{"id": 1}], sciezka)
    assert sciezka.exists()
    assert json.loads(sciezka.read_text(encoding="utf-8")) == [{"id": 1}]


def test_zapisz_json_zachowuje_polskie_znaki(tmp_path: Path):
    sciezka = tmp_path / "podsumowanie.json"
    zapisz_json({"kontrahent": "Żółw sp. z o.o."}, sciezka)
    tresc = sciezka.read_text(encoding="utf-8")
    assert "Żółw" in tresc  # ensure_ascii=False — czytelne bez odkodowywania \u escape'ów
