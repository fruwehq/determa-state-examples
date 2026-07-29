from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from order_service.api import create_app
from order_service.settings import Settings

from .conftest import aggregate_order_status, create_order, send


def app_client(database_path: Path, version: int) -> TestClient:
    return TestClient(
        create_app(Settings(database_path=database_path, definition_version=version))
    )


def test_restart_restores_canonical_aggregate(database_path: Path) -> None:
    with app_client(database_path, 2) as first:
        order = create_order(first, key="create-restart")
        order_id = str(order["order_id"])
        paid = send(first, order_id, "payment_accepted", "payment-before-restart")

    with app_client(database_path, 2) as restarted:
        restored = restarted.get(f"/orders/{order_id}")
        assert restored.status_code == 200
        assert restored.json()["aggregate_digest"] == paid["aggregate_digest"]
        completed = send(
            restarted,
            order_id,
            "fulfillment_succeeded",
            "fulfillment-after-restart",
        )
        assert completed["active_state"] == "completed"
        assert completed["lifecycle_status"] == "completed"
        assert aggregate_order_status(database_path, order_id) == "completed"


def test_next_command_lazily_migrates_and_remaps_active_state(
    database_path: Path,
) -> None:
    with app_client(database_path, 1) as old_deployment:
        order = create_order(old_deployment, key="create-old")
        order_id = str(order["order_id"])
        send(old_deployment, order_id, "payment_accepted", "pay-old")
        old = send(old_deployment, order_id, "fulfillment_started", "start-old")
        assert old["machine_version"] == 1
        assert old["active_state"] == "fulfilling"

    with app_client(database_path, 2) as new_deployment:
        untouched = new_deployment.get(f"/orders/{order_id}").json()
        assert untouched["machine_version"] == 1
        assert untouched["active_state"] == "fulfilling"

        migrated = send(
            new_deployment,
            order_id,
            "fulfillment_succeeded",
            "finish-new",
        )
        assert migrated["migration_applied"] is True
        assert migrated["machine_version"] == 2
        assert migrated["active_state"] == "completed"
        debug = new_deployment.get(f"/admin/orders/{order_id}/debug").json()
        assert len(debug["migration_audits"]) == 1


def test_maintenance_endpoint_explicitly_remaps_fulfilling_to_shipping(
    database_path: Path,
) -> None:
    with app_client(database_path, 1) as old_deployment:
        order = create_order(old_deployment, key="create-maintenance")
        order_id = str(order["order_id"])
        send(old_deployment, order_id, "payment_accepted", "pay-maintenance")
        send(old_deployment, order_id, "fulfillment_started", "start-maintenance")

    with app_client(database_path, 2) as new_deployment:
        response = new_deployment.post(f"/admin/orders/{order_id}/migrate")
        assert response.status_code == 200, response.text
        migrated = response.json()
        assert migrated["migration_applied"] is True
        assert migrated["machine_version"] == 2
        assert migrated["active_state"] == "shipping"
        assert migrated["lifecycle_status"] == "shipping"
        assert aggregate_order_status(database_path, order_id) == "shipping"

        repeated = new_deployment.post(f"/admin/orders/{order_id}/migrate").json()
        assert repeated["migration_applied"] is False
        assert repeated["aggregate_digest"] == migrated["aggregate_digest"]
