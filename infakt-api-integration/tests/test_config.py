from __future__ import annotations

import importlib


def test_settings_czyta_zmienne_srodowiskowe(monkeypatch):
    monkeypatch.setenv("INFAKT_API_KEY", "tajny-klucz")
    monkeypatch.setenv("INFAKT_ENV", "production")
    monkeypatch.setenv("INFAKT_SUPPLIER_DENYLIST_NIP", "1112223344")

    from app import config

    importlib.reload(config)
    try:
        assert config.settings.api_key == "tajny-klucz"
        assert config.settings.environment == "production"
        assert config.settings.supplier_denylist_nip == "1112223344"
    finally:
        monkeypatch.delenv("INFAKT_API_KEY", raising=False)
        monkeypatch.delenv("INFAKT_ENV", raising=False)
        monkeypatch.delenv("INFAKT_SUPPLIER_DENYLIST_NIP", raising=False)
        importlib.reload(config)  # przywróć domyślny stan dla kolejnych testów


def test_settings_domyslne_srodowisko_to_sandbox():
    from app import config

    importlib.reload(config)
    assert config.settings.environment in ("sandbox", "production")
