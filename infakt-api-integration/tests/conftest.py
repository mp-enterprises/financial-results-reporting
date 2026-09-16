from __future__ import annotations

import pytest

from app.client import InfaktClient


@pytest.fixture
def client() -> InfaktClient:
    """Klient testowy — bez realnych zapytań sieciowych (mockowanych przez `responses`)."""
    return InfaktClient(api_key="test-klucz", environment="sandbox", max_retries=2, sleep_fn=lambda _: None)


@pytest.fixture
def faktura_przykladowa() -> dict:
    return {"id": 1, "number": "FV/1/2026", "gross_price": 123456, "invoice_date": "2026-09-05"}


@pytest.fixture
def koszt_przykladowy() -> dict:
    return {
        "id": 10,
        "gross_price": 50000,
        "seller_tax_code_number": "111-222-33-44",
        "issue_date": "2026-09-10",
    }
