from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from order_service.api import create_app
from order_service.settings import Settings


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "orders.sqlite3"


@pytest.fixture
def client(database_path: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(Settings(database_path=database_path, definition_version=2))
    ) as active:
        yield active


def create_order(
    client: TestClient,
    *,
    key: str = "create-order-1",
    customer_id: str = "customer-42",
    amount_cents: int = 12900,
) -> dict[str, object]:
    response = client.post(
        "/orders",
        headers={"Idempotency-Key": key},
        json={"customer_id": customer_id, "amount_cents": amount_cents},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def send(
    client: TestClient,
    order_id: str,
    event: str,
    key: str,
    *,
    reason: str | None = None,
) -> dict[str, object]:
    payload = {} if reason is None else {"reason": reason}
    response = client.post(
        f"/orders/{order_id}/events/{event}",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())
