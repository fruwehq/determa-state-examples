from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, cast

import determa.state as ds

from .database import Database
from .definitions import DefinitionRegistry


class OrderNotFoundError(LookupError):
    pass


class DuplicateConflictError(ValueError):
    pass


class CommandRejectedError(ValueError):
    pass


@dataclass(frozen=True)
class CommandResult:
    view: dict[str, Any]
    duplicate: bool
    migration_applied: bool


class OrderService:
    def __init__(
        self,
        database: Database,
        definitions: DefinitionRegistry,
        definition_version: int,
    ) -> None:
        self.database = database
        self.definitions = definitions
        self.current_bundle = definitions.bundle(definition_version)

    def create_order(
        self,
        *,
        event_id: str,
        customer_id: str,
        amount_cents: int,
    ) -> CommandResult:
        order_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"determa-order:{event_id}"))
        payload = {"customer_id": customer_id, "amount_cents": amount_cents}
        with self.database.transaction() as connection:
            duplicate = self._duplicate(connection, event_id, order_id, "create_order", payload)
            if duplicate is not None:
                return CommandResult(duplicate, True, False)
            created = ds.create(
                self.current_bundle,
                machine_id="order",
                root_instance_id=order_id,
                creation_id=event_id,
                bindings={
                    "input": {
                        "order_id": order_id,
                        "customer_id": customer_id,
                        "amount_cents": amount_cents,
                    }
                },
            )
            state = created["state"]
            if state is None or created["status"] == "faulted":
                raise CommandRejectedError(f"creation failed: {created['fault']}")
            aggregate = ds.serialize_aggregate(self.current_bundle, state)
            view = self._view_from_state(
                order_id, customer_id, amount_cents, self.current_bundle, state, aggregate
            )
            connection.execute(
                """
                INSERT INTO orders (
                    order_id, customer_id, amount_cents, lifecycle_status,
                    definition_fingerprint, aggregate_bytes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    customer_id,
                    amount_cents,
                    view["lifecycle_status"],
                    self.current_bundle.fingerprint,
                    aggregate,
                ),
            )
            self._store_emissions(connection, order_id, created["emissions"])
            self._record_inbox(
                connection, event_id, order_id, "create_order", payload, view
            )
            return CommandResult(view, False, False)

    def apply_event(
        self,
        *,
        order_id: str,
        event_id: str,
        event_name: str,
        payload: dict[str, Any],
    ) -> CommandResult:
        with self.database.transaction() as connection:
            row = self._order_row(connection, order_id)
            duplicate = self._duplicate(connection, event_id, order_id, event_name, payload)
            if duplicate is not None:
                return CommandResult(duplicate, True, False)
            restored = ds.restore_aggregate(
                bytes(row["aggregate_bytes"]), self.definitions.resolver
            )
            current_machine = self.current_bundle.machine("order")
            assert current_machine is not None
            projected_status = self._project_status(
                str(row["lifecycle_status"]),
                event_name,
                int(current_machine["version"]),
            )
            target = {
                "root": {
                    "root_instance_id": restored.state["root_instance_id"],
                    "root_runtime_id": restored.state["root_runtime_id"],
                }
            }
            envelope = {
                "event": event_name,
                "event_id": event_id,
                "target": target,
                "payload": payload,
            }
            declaration = (self.current_bundle.raw.get("events") or {}).get(event_name, {})
            if declaration.get("correlates_to"):
                envelope["correlation_id"] = order_id
            route = self.definitions.route(
                restored.bundle.fingerprint, self.current_bundle.fingerprint
            )
            if route:
                outcome = ds.migrate_and_dispatch(
                    bytes(row["aggregate_bytes"]),
                    self.current_bundle.fingerprint,
                    route,
                    self.definitions.resolver,
                    {"input": envelope},
                    maintenance_mode=False,
                )
                if outcome.failure is not None:
                    raise CommandRejectedError(f"migration failed: {outcome.failure.code}")
                aggregate = outcome.aggregate_bytes
                emissions = list(outcome.emissions)
                disposition = outcome.disposition
                rejection = outcome.rejection
                audits = list(outcome.audit_records)
                migration_applied = True
            else:
                core = ds.dispatch(
                    self.current_bundle,
                    restored.state,
                    {"input": envelope},
                )
                state = core["state"]
                if state is None:
                    raise CommandRejectedError("dispatch returned no aggregate")
                aggregate = ds.serialize_aggregate(self.current_bundle, state)
                emissions = core["emissions"]
                disposition = core["disposition"]
                rejection = core["rejection"]
                audits = []
                migration_applied = False
            if aggregate is None or disposition != "handled":
                detail = rejection or {"code": disposition or "unknown"}
                raise CommandRejectedError(f"event was not handled: {detail}")
            restored_result = ds.restore_aggregate(aggregate, self.definitions.resolver)
            view = self._view_from_state(
                order_id,
                str(row["customer_id"]),
                int(row["amount_cents"]),
                restored_result.bundle,
                restored_result.state,
                aggregate,
                lifecycle_status_hint=projected_status,
            )
            connection.execute(
                """
                UPDATE orders
                SET lifecycle_status = ?, definition_fingerprint = ?,
                    aggregate_bytes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                """,
                (
                    view["lifecycle_status"],
                    restored_result.bundle.fingerprint,
                    aggregate,
                    order_id,
                ),
            )
            self._store_emissions(connection, order_id, emissions)
            for audit in audits:
                connection.execute(
                    """
                    INSERT INTO migration_audits (
                        order_id, migration_sequence, descriptor_digest, audit_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        audit["migration_sequence"],
                        audit["migration_descriptor_digest"],
                        self._json(audit),
                    ),
                )
            self._record_inbox(
                connection, event_id, order_id, event_name, payload, view
            )
            return CommandResult(view, False, migration_applied)

    def migrate_order(self, order_id: str) -> CommandResult:
        with self.database.transaction() as connection:
            row = self._order_row(connection, order_id)
            restored = ds.restore_aggregate(
                bytes(row["aggregate_bytes"]), self.definitions.resolver
            )
            route = self.definitions.route(
                restored.bundle.fingerprint, self.current_bundle.fingerprint
            )
            if not route:
                view = self._view_from_state(
                    order_id,
                    str(row["customer_id"]),
                    int(row["amount_cents"]),
                    restored.bundle,
                    restored.state,
                    bytes(row["aggregate_bytes"]),
                    lifecycle_status_hint=str(row["lifecycle_status"]),
                )
                return CommandResult(view, False, False)
            outcome = ds.migrate_aggregate(
                bytes(row["aggregate_bytes"]),
                self.current_bundle.fingerprint,
                route,
                self.definitions.resolver,
                maintenance_mode=True,
            )
            if outcome.failure is not None or outcome.aggregate_bytes is None:
                code = outcome.failure.code if outcome.failure is not None else "missing_aggregate"
                raise CommandRejectedError(f"migration failed: {code}")
            restored_result = ds.restore_aggregate(
                outcome.aggregate_bytes, self.definitions.resolver
            )
            view = self._view_from_state(
                order_id,
                str(row["customer_id"]),
                int(row["amount_cents"]),
                restored_result.bundle,
                restored_result.state,
                outcome.aggregate_bytes,
                lifecycle_status_hint=str(row["lifecycle_status"]),
            )
            connection.execute(
                """
                UPDATE orders
                SET lifecycle_status = ?, definition_fingerprint = ?,
                    aggregate_bytes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                """,
                (
                    view["lifecycle_status"],
                    restored_result.bundle.fingerprint,
                    outcome.aggregate_bytes,
                    order_id,
                ),
            )
            for audit in outcome.audit_records:
                connection.execute(
                    """
                    INSERT INTO migration_audits (
                        order_id, migration_sequence, descriptor_digest, audit_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        audit["migration_sequence"],
                        audit["migration_descriptor_digest"],
                        self._json(audit),
                    ),
                )
            return CommandResult(view, False, True)

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._order_row(connection, order_id)
            restored = ds.restore_aggregate(
                bytes(row["aggregate_bytes"]), self.definitions.resolver
            )
            return self._view_from_state(
                order_id,
                str(row["customer_id"]),
                int(row["amount_cents"]),
                restored.bundle,
                restored.state,
                bytes(row["aggregate_bytes"]),
                lifecycle_status_hint=str(row["lifecycle_status"]),
            )

    def list_outbox(self, order_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM outbox"
        parameters: tuple[str, ...] = ()
        if order_id is not None:
            query += " WHERE order_id = ?"
            parameters = (order_id,)
        query += " ORDER BY order_id, sequence"
        with self.database.connect() as connection:
            return [self._outbox_view(row) for row in connection.execute(query, parameters)]

    def deliver_outbox(self) -> dict[str, int]:
        attempted = 0
        newly_delivered = 0
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT effect_id FROM outbox ORDER BY order_id, sequence"
            ).fetchall()
            for row in rows:
                attempted += 1
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO effect_deliveries (effect_id, adapter_name)
                    VALUES (?, 'local-recording-adapter')
                    """,
                    (row["effect_id"],),
                ).rowcount
                connection.execute(
                    """
                    UPDATE outbox
                    SET delivery_status = 'delivered',
                        delivery_attempts = delivery_attempts + 1,
                        delivered_at = COALESCE(delivered_at, CURRENT_TIMESTAMP)
                    WHERE effect_id = ?
                    """,
                    (row["effect_id"],),
                )
                newly_delivered += inserted
        return {"attempted": attempted, "newly_delivered": newly_delivered}

    def debug_order(self, order_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._order_row(connection, order_id)
            inbox = [
                {
                    "event_id": row["event_id"],
                    "event_name": row["event_name"],
                    "payload": json.loads(row["payload_json"]),
                    "accepted_at": row["accepted_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM inbox WHERE order_id = ? ORDER BY accepted_at, event_id",
                    (order_id,),
                )
            ]
            audits = [
                json.loads(row["audit_json"])
                for row in connection.execute(
                    """
                    SELECT audit_json FROM migration_audits
                    WHERE order_id = ? ORDER BY CAST(migration_sequence AS INTEGER)
                    """,
                    (order_id,),
                )
            ]
        return {
            "order": self.get_order(order_id),
            "inbox": inbox,
            "outbox": self.list_outbox(order_id),
            "migration_audits": audits,
        }

    def _order_row(self, connection: sqlite3.Connection, order_id: str) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone(),
        )
        if row is None:
            raise OrderNotFoundError(order_id)
        return row

    def _duplicate(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        order_id: str,
        event_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM inbox WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        if (
            row["order_id"] != order_id
            or row["event_name"] != event_name
            or row["payload_json"] != self._json(payload)
        ):
            raise DuplicateConflictError("idempotency key was reused for a different command")
        return cast(dict[str, Any], json.loads(row["response_json"]))

    def _record_inbox(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        order_id: str,
        event_name: str,
        payload: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO inbox (
                event_id, order_id, event_name, payload_json, response_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                order_id,
                event_name,
                self._json(payload),
                self._json(response),
            ),
        )

    def _store_emissions(
        self,
        connection: sqlite3.Connection,
        order_id: str,
        emissions: list[dict[str, Any]],
    ) -> None:
        for emission in emissions:
            if emission.get("target") != "external":
                raise CommandRejectedError("this host only accepts external output intents")
            connection.execute(
                """
                INSERT INTO outbox (
                    effect_id, order_id, sequence, event_name,
                    correlation_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    emission["effect_id"],
                    order_id,
                    emission["sequence"],
                    emission["event"],
                    emission["correlation_id"],
                    self._json(emission["payload"]),
                ),
            )

    def _view_from_state(
        self,
        order_id: str,
        customer_id: str,
        amount_cents: int,
        bundle: ds.Bundle,
        state: dict[str, Any],
        aggregate: bytes,
        lifecycle_status_hint: str | None = None,
    ) -> dict[str, Any]:
        root = state["runtimes"][state["root_runtime_id"]]
        active = list(root["active"])
        active_state = (
            str(active[-1])
            if active
            else lifecycle_status_hint or str(root["status"])
        )
        envelope = json.loads(aggregate)
        machine = bundle.machine("order")
        assert machine is not None
        return {
            "order_id": order_id,
            "customer_id": customer_id,
            "amount_cents": amount_cents,
            "lifecycle_status": active_state,
            "active_state": active_state,
            "machine_version": int(machine["version"]),
            "definition_fingerprint": bundle.fingerprint,
            "aggregate_digest": envelope["aggregate_state_digest"],
        }

    def _outbox_view(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "effect_id": row["effect_id"],
            "order_id": row["order_id"],
            "sequence": row["sequence"],
            "event_name": row["event_name"],
            "correlation_id": row["correlation_id"],
            "payload": json.loads(row["payload_json"]),
            "delivery_status": row["delivery_status"],
            "delivery_attempts": row["delivery_attempts"],
        }

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _project_status(current: str, event: str, machine_version: int) -> str:
        shipping = "fulfilling" if machine_version == 1 else "shipping"
        transitions = {
            ("awaiting_payment", "payment_accepted"): "awaiting_fulfillment",
            ("awaiting_payment", "payment_rejected"): "payment_rejected",
            ("awaiting_payment", "cancel_requested"): "cancelled",
            ("awaiting_fulfillment", "fulfillment_started"): shipping,
            ("awaiting_fulfillment", "fulfillment_succeeded"): "completed",
            ("awaiting_fulfillment", "fulfillment_failed"): "failed",
            ("awaiting_fulfillment", "cancel_requested"): "cancellation_pending",
            ("fulfilling", "fulfillment_succeeded"): "completed",
            ("fulfilling", "fulfillment_failed"): "failed",
            ("fulfilling", "cancel_requested"): "cancellation_pending",
            ("shipping", "fulfillment_succeeded"): "completed",
            ("shipping", "fulfillment_failed"): "failed",
            ("shipping", "cancel_requested"): "cancellation_pending",
            ("cancellation_pending", "fulfillment_cancelled"): "cancelled",
            ("cancellation_pending", "fulfillment_cancellation_failed"): "failed",
        }
        return transitions.get((current, event), current)
