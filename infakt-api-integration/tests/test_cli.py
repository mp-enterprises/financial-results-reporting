from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import responses

from app import cli
from app.client import BASE_URLS
from app.resources import WSZYSTKIE_ZASOBY


def _ustaw_testowy_klucz(monkeypatch):
    monkeypatch.setattr("app.cli.settings", dataclasses.replace(cli.settings, api_key="test-klucz"))


@responses.activate
def test_test_connection_zwraca_0_gdy_ok(monkeypatch, capsys):
    _ustaw_testowy_klucz(monkeypatch)
    responses.add(responses.GET, f"{BASE_URLS['sandbox']}/invoices.json", json=[], status=200)

    kod = cli.main(["test-connection"])

    assert kod == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


@responses.activate
def test_test_connection_zwraca_1_gdy_401(monkeypatch, capsys):
    _ustaw_testowy_klucz(monkeypatch)
    responses.add(
        responses.GET, f"{BASE_URLS['sandbox']}/invoices.json", json={"error": "unauthorized"}, status=401
    )

    kod = cli.main(["test-connection"])

    assert kod == 1
    assert "nieudane" in capsys.readouterr().err.lower()


@responses.activate
def test_fetch_zapisuje_pliki_i_zwraca_0(monkeypatch, capsys, tmp_path: Path):
    _ustaw_testowy_klucz(monkeypatch)
    for _, sciezka in WSZYSTKIE_ZASOBY.items():
        responses.add(responses.GET, f"{BASE_URLS['sandbox']}/{sciezka}", json=[], status=200)

    kod = cli.main(["fetch", "--okres", "2026-09", "--output-dir", str(tmp_path)])

    assert kod == 0
    podsumowanie = json.loads(capsys.readouterr().out)
    assert podsumowanie["okres"] == "2026-09"
    assert (tmp_path / "summary" / "2026-09.json").exists()


def test_fetch_wymaga_okresu(monkeypatch, capsys):
    _ustaw_testowy_klucz(monkeypatch)
    try:
        cli.main(["fetch"])
        raise AssertionError("oczekiwano SystemExit — argparse powinien wymagać --okres")
    except SystemExit as exc:
        assert exc.code != 0
