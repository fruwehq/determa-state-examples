"""A durable embedded workflow; SDK objects never enter machine persistence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import determa.state as ds
from determa.state.wire import canonical_bytes, decoded_typed_value
from google.api_core.exceptions import AlreadyExists
from google.auth.credentials import AnonymousCredentials
from google.cloud import tasks_v2

ROOT = Path(__file__).resolve().parent


class CloudTasksHandler:
    """Named application-owned adapter; no verified §19 host profile is advertised."""

    def __init__(self, client: tasks_v2.CloudTasksClient, queue: str) -> None:
        self.client = client
        self.queue = queue

    def invoke(self, intent: dict[str, Any]) -> dict[str, str]:
        payload = decoded_typed_value(intent["payload"])
        identity = hashlib.sha256(intent["effect_id"].encode()).hexdigest()
        # These are actual SDK proto-plus/protobuf values, private to this handler.
        task = tasks_v2.Task(
            name=f"{self.queue}/tasks/{identity}",
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=payload["callback_url"],
                headers={"Content-Type": "application/json"},
                body=payload["body"].encode(),
            ),
        )
        request = tasks_v2.CreateTaskRequest(parent=self.queue, task=task)
        try:
            created = self.client.create_task(request=request)
        except AlreadyExists:
            created = self.client.get_task(
                request=tasks_v2.GetTaskRequest(
                    name=task.name, response_view=tasks_v2.Task.View.FULL
                )
            )
            if created.http_request != task.http_request:
                raise ValueError("existing task differs from committed intent") from None
        if created.name != task.name:
            raise ValueError("Cloud Tasks returned a different task identity")
        return {"task_name": created.name}


class Workflow:
    def __init__(self, path: Path, handlers: dict[str, CloudTasksHandler]) -> None:
        if "cloud-tasks.v1" not in handlers:
            raise ValueError("cloud-tasks.v1 handler must be installed before creating work")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.handlers = handlers
        self.bundle = ds.load_bundle((ROOT / "machines/workflow.yaml").read_text())
        self.resolver = ds.MemoryArtifactResolver(
            definitions={self.bundle.fingerprint: self.bundle}
        )
        with self.connect() as connection:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS workflows "
                "(id TEXT PRIMARY KEY, aggregate BLOB NOT NULL);"
                "CREATE TABLE IF NOT EXISTS effects "
                "(effect_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, "
                "handler TEXT NOT NULL, intent BLOB NOT NULL, outcome BLOB);"
                "CREATE TABLE IF NOT EXISTS handler_bindings "
                "(handler TEXT PRIMARY KEY NOT NULL, queue TEXT NOT NULL);"
                "CREATE TRIGGER IF NOT EXISTS bindings_no_update BEFORE UPDATE ON handler_bindings "
                "BEGIN SELECT RAISE(ABORT, 'immutable handler binding'); END;"
                "CREATE TRIGGER IF NOT EXISTS bindings_no_delete BEFORE DELETE ON handler_bindings "
                "BEGIN SELECT RAISE(ABORT, 'immutable handler binding'); END;"
            )

            unbound = connection.execute(
                "SELECT 1 FROM effects e WHERE NOT EXISTS "
                "(SELECT 1 FROM handler_bindings b WHERE b.handler=e.handler) LIMIT 1"
            ).fetchone()
            if unbound is not None:
                raise ValueError("existing unbound intent cannot be assigned a destination")
            for name, handler in handlers.items():
                connection.execute(
                    "INSERT OR IGNORE INTO handler_bindings VALUES (?,?)", (name, handler.queue)
                )
                self.verify_binding(connection, name)

    def verify_binding(self, connection: sqlite3.Connection, name: str) -> None:
        if name not in self.handlers:
            raise ValueError("required handler is not installed")
        row = connection.execute(
            "SELECT queue FROM handler_bindings WHERE handler=?", (name,)
        ).fetchone()
        if row is None or row[0] != self.handlers[name].queue:
            raise ValueError("installed handler destination differs from immutable binding")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def enqueue(self, identity: str, callback_url: str, body: str) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.verify_binding(connection, "cloud-tasks.v1")
            if connection.execute("SELECT 1 FROM workflows WHERE id=?", (identity,)).fetchone():
                raise ValueError("workflow already exists; inspect or resume it")
            created = ds.create(
                self.bundle,
                machine_id="task_workflow",
                root_instance_id=identity,
                creation_id=f"{identity}:create",
                bindings={},
            )
            result = self.process(
                created["state"],
                "enqueue",
                f"{identity}:enqueue",
                {"callback_url": callback_url, "body": body},
            )
            connection.execute(
                "INSERT INTO workflows VALUES (?,?)", (identity, canonical_bytes(result["state"]))
            )
            for intent in result["emissions"]:
                connection.execute(
                    "INSERT INTO effects VALUES (?,?,?,?,NULL)",
                    (intent["effect_id"], identity, "cloud-tasks.v1", canonical_bytes(intent)),
                )
            connection.commit()
        # The handler is invoked only by drain(), after this transaction committed.

    def process(
        self,
        state: dict[str, Any],
        event: str,
        event_id: str,
        payload: dict[str, str],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        target = {
            "root": {
                "root_instance_id": state["root_instance_id"],
                "root_runtime_id": state["root_runtime_id"],
            }
        }
        envelope = ds.portable_envelope(
            event, event_id, target, payload, correlation_id=correlation_id
        )
        admitted = ds.admit(
            state,
            [
                {
                    "delivery_mode": "input",
                    "envelope": envelope,
                    "envelope_digest": ds.delivery_request_digest(
                        state["root_instance_id"], "input", envelope
                    ),
                }
            ],
            self.resolver,
        )
        if admitted["result"] != "accepted":
            raise ValueError(f"admission refused: {admitted}")
        result = ds.step(admitted["state"], state["root_runtime_id"], self.resolver)
        if result["disposition"] != "handled":
            raise ValueError(f"workflow event failed: {result['disposition']}")
        return result

    def drain(self) -> int:
        with self.connect() as connection:
            pending = connection.execute(
                "SELECT effect_id, workflow_id, handler, intent FROM effects "
                "WHERE outcome IS NULL ORDER BY effect_id"
            ).fetchall()
        completed = 0
        for effect_id, workflow_id, handler_name, raw in pending:
            intent = json.loads(raw)
            with self.connect() as connection:
                self.verify_binding(connection, handler_name)
            outcome = self.handlers[handler_name].invoke(intent)
            # External SDK work cannot join this local commit. Stable task names reduce
            # duplicate submission; Cloud Tasks retention is finite, not exactly-once proof.
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if (
                    connection.execute(
                        "SELECT outcome FROM effects WHERE effect_id=?", (effect_id,)
                    ).fetchone()[0]
                    is not None
                ):
                    connection.rollback()
                    continue
                row = connection.execute(
                    "SELECT aggregate FROM workflows WHERE id=?", (workflow_id,)
                ).fetchone()
                result = self.process(
                    json.loads(row[0]),
                    "task_created",
                    f"result:{effect_id}",
                    outcome,
                    intent["correlation_id"],
                )
                connection.execute(
                    "UPDATE workflows SET aggregate=? WHERE id=?",
                    (canonical_bytes(result["state"]), workflow_id),
                )
                connection.execute(
                    "UPDATE effects SET outcome=? WHERE effect_id=?",
                    (canonical_bytes(outcome), effect_id),
                )
                connection.commit()
                completed += 1
        return completed

    def inspect(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            for (raw,) in connection.execute("SELECT aggregate FROM workflows"):
                ds.restore_aggregate_v1(bytes(raw), self.resolver)
            return [
                {"workflow_id": identity, "aggregate": json.loads(raw)}
                for identity, raw in connection.execute(
                    "SELECT id, aggregate FROM workflows ORDER BY id"
                )
            ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["demo", "inspect"])
    parser.add_argument("--database", type=Path, default=ROOT / "var/workflow.sqlite3")
    args = parser.parse_args()
    # Runnable offline demo uses real SDK and protobuf types, with mocked transport.
    client = tasks_v2.CloudTasksClient(credentials=AnonymousCredentials())
    client.create_task = Mock(side_effect=lambda *, request: request.task)
    handler = CloudTasksHandler(client, "projects/demo/locations/us-central1/queues/demo")
    workflow = Workflow(args.database, {"cloud-tasks.v1": handler})
    if args.command == "demo":
        workflow.enqueue("demo", "https://example.com/task", '{"order_id":"demo"}')
        assert workflow.drain() == 1
    print(json.dumps(workflow.inspect(), indent=2))


if __name__ == "__main__":
    main()
