from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import create_order, send


def test_happy_path_persists_effects_and_requires_later_outcomes(
    client: TestClient,
) -> None:
    created = create_order(client)
    order_id = str(created["order_id"])
    assert created["active_state"] == "awaiting_payment"

    outbox = client.get("/admin/outbox", params={"order_id": order_id}).json()
    assert [item["event_name"] for item in outbox] == ["payment_requested"]
    assert outbox[0]["delivery_status"] == "pending"

    delivery = client.post("/admin/outbox/deliver").json()
    assert delivery == {"attempted": 1, "newly_delivered": 1}
    assert client.get(f"/orders/{order_id}").json()["active_state"] == "awaiting_payment"

    paid = send(client, order_id, "payment_accepted", "payment-accepted-1")
    assert paid["active_state"] == "awaiting_fulfillment"
    started = send(client, order_id, "fulfillment_started", "fulfillment-started-1")
    assert started["active_state"] == "shipping"
    completed = send(client, order_id, "fulfillment_succeeded", "fulfillment-done-1")
    assert completed["active_state"] == "completed"

    events = [
        item["event_name"]
        for item in client.get("/admin/outbox", params={"order_id": order_id}).json()
    ]
    assert events == ["payment_requested", "fulfillment_requested"]


def test_payment_rejection_and_fulfillment_failure_are_terminal(
    client: TestClient,
) -> None:
    rejected = create_order(client, key="create-rejected")
    rejected_id = str(rejected["order_id"])
    outcome = send(
        client,
        rejected_id,
        "payment_rejected",
        "payment-rejected-1",
        reason="card declined",
    )
    assert outcome["active_state"] == "payment_rejected"

    failed = create_order(client, key="create-failed")
    failed_id = str(failed["order_id"])
    send(client, failed_id, "payment_accepted", "payment-accepted-failed-order")
    outcome = send(
        client,
        failed_id,
        "fulfillment_failed",
        "fulfillment-failed-1",
        reason="warehouse unavailable",
    )
    assert outcome["active_state"] == "failed"


def test_cancellation_waits_for_external_confirmation(client: TestClient) -> None:
    order = create_order(client, key="create-cancel")
    order_id = str(order["order_id"])
    send(client, order_id, "payment_accepted", "payment-accepted-cancel")

    pending = send(client, order_id, "cancel_requested", "cancel-requested-1")
    assert pending["active_state"] == "cancellation_pending"
    assert client.get(f"/orders/{order_id}").json()["active_state"] == (
        "cancellation_pending"
    )
    assert client.get("/admin/outbox", params={"order_id": order_id}).json()[-1][
        "event_name"
    ] == "fulfillment_cancellation_requested"

    cancelled = send(
        client,
        order_id,
        "fulfillment_cancelled",
        "fulfillment-cancelled-1",
    )
    assert cancelled["active_state"] == "cancelled"


def test_duplicate_commands_do_not_repeat_state_changes_or_effects(
    client: TestClient,
) -> None:
    first = create_order(client, key="same-create")
    duplicate = create_order(client, key="same-create")
    assert duplicate["duplicate"] is True
    assert duplicate["aggregate_digest"] == first["aggregate_digest"]
    order_id = str(first["order_id"])
    assert len(client.get("/admin/outbox", params={"order_id": order_id}).json()) == 1

    accepted = send(client, order_id, "payment_accepted", "same-event")
    duplicate_event = send(client, order_id, "payment_accepted", "same-event")
    assert duplicate_event["duplicate"] is True
    assert duplicate_event["aggregate_digest"] == accepted["aggregate_digest"]
    assert len(client.get("/admin/outbox", params={"order_id": order_id}).json()) == 2

    conflict = client.post(
        "/orders",
        headers={"Idempotency-Key": "same-create"},
        json={"customer_id": "different", "amount_cents": 100},
    )
    assert conflict.status_code == 409


def test_outbox_adapter_is_idempotent_by_effect_id(client: TestClient) -> None:
    order = create_order(client, key="create-delivery")
    order_id = str(order["order_id"])

    first = client.post("/admin/outbox/deliver").json()
    second = client.post("/admin/outbox/deliver").json()
    assert first == {"attempted": 1, "newly_delivered": 1}
    assert second == {"attempted": 1, "newly_delivered": 0}

    row = client.get("/admin/outbox", params={"order_id": order_id}).json()[0]
    assert row["delivery_status"] == "delivered"
    assert row["delivery_attempts"] == 2


def test_debug_endpoint_exposes_inbox_outbox_and_aggregate_identity(
    client: TestClient,
) -> None:
    order = create_order(client, key="create-debug")
    order_id = str(order["order_id"])
    send(client, order_id, "payment_accepted", "payment-debug")

    debug = client.get(f"/admin/orders/{order_id}/debug")
    assert debug.status_code == 200
    body = debug.json()
    assert [item["event_name"] for item in body["inbox"]] == [
        "create_order",
        "payment_accepted",
    ]
    assert [item["event_name"] for item in body["outbox"]] == [
        "payment_requested",
        "fulfillment_requested",
    ]
    assert body["order"]["aggregate_digest"].startswith("sha256:")


def test_unhandled_event_rolls_back_inbox_and_aggregate(client: TestClient) -> None:
    order = create_order(client, key="create-unhandled")
    order_id = str(order["order_id"])
    before = client.get(f"/orders/{order_id}").json()

    response = client.post(
        f"/orders/{order_id}/events/fulfillment_started",
        headers={"Idempotency-Key": "too-early"},
        json={},
    )
    assert response.status_code == 409
    after = client.get(f"/orders/{order_id}").json()
    assert after["aggregate_digest"] == before["aggregate_digest"]
    debug = client.get(f"/admin/orders/{order_id}/debug").json()
    assert [item["event_id"] for item in debug["inbox"]] == ["create-unhandled"]
