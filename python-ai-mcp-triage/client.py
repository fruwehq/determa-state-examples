"""Triage application client: no local copy of remotely committed machine state."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.request
import uuid
from pathlib import Path

import determa.state as ds
from determa.state.public_client import EndpointBinding, PublicHostClient
from determa.state.wire import canonical_bytes, strict_json

ROOT = Path(__file__).resolve().parent


def http_transport(token):
    def send(endpoint, request):
        outgoing = urllib.request.Request(
            endpoint,
            data=canonical_bytes(request),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
        )
        with urllib.request.urlopen(outgoing, timeout=10) as response:
            document, _ = strict_json(response.read())
            return document

    return send


def request(operation, identity, operation_id=None):
    return {
        "protocol": "determa.execution_host",
        "protocol_version": 1,
        "scope_binding_identity": None,
        "operation": operation,
        "operation_id": operation_id or str(uuid.uuid4()),
        "target": {"root_instance_id": identity, "runtime_id": None, "runtime_incarnation": None},
        "precondition": None,
        "arguments": {},
    }


def checked(response):
    if response["status"] != "committed":
        raise ValueError(f"public operation refused: {response['error']}")
    return response["value"]["result"]


class TriageClient:
    def __init__(self, journal: Path, endpoint: str, token: str, transport=None):
        journal.parent.mkdir(parents=True, exist_ok=True)
        self.journal = journal
        self.endpoint = endpoint
        self.transport = transport or http_transport(token)
        self.client = PublicHostClient(
            journal,
            {"triage": EndpointBinding(endpoint, "triage")},
            self.transport,
        )
        self.client.setup_schema()
        self.command_path = journal.with_name(journal.stem + "-commands.sqlite3")
        with self.command_connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS commands "
                "(event_id TEXT PRIMARY KEY, command BLOB NOT NULL, "
                "endpoint TEXT NOT NULL, admit BLOB NOT NULL, process BLOB)"
            )

    def command_connection(self):
        connection = sqlite3.connect(self.command_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def create(self, identity):
        bundle = ds.load_bundle((ROOT / "machines/triage.yaml").read_text())
        candidate = request("create", identity, f"{identity}:create")
        candidate["arguments"] = {
            "validated_bundle_fingerprint": bundle.fingerprint,
            "namespace": "examples.ai_triage",
            "machine_id": "triage",
            "machine_version": "1",
            "root_instance_id": identity,
            "creation_id": f"{identity}:create",
            "bindings": ["map", []],
        }
        return checked(self.client.submit("triage", candidate))["checkpoint"]

    def read(self, identity):
        return checked(self.client.submit("triage", request("read", identity)))["checkpoint"]

    def event(self, identity, event, event_id, payload=None, correlation_id=None):
        command = canonical_bytes(
            {
                "identity": identity,
                "event": event,
                "payload": payload or {},
                "correlation_id": correlation_id,
            }
        )
        with self.command_connection() as connection:
            saved = connection.execute(
                "SELECT command,admit,process,endpoint FROM commands WHERE event_id=?", (event_id,)
            ).fetchone()
        if saved is not None and saved[0] != command:
            raise ValueError("event ID reused for a different application command")
        if saved is None:
            checkpoint = self.read(identity)
            aggregate = checkpoint["root_record"]["aggregate_state"]
            runtime = next(
                r for r in aggregate["runtimes"] if r["runtime_id"] == aggregate["root_runtime_id"]
            )
            if runtime["ready_mailbox"] or runtime["deferred_mailbox"]:
                raise ValueError("resume outstanding mailbox work before a new command")
            envelope = ds.portable_envelope(
                event,
                event_id,
                runtime["target_identity"],
                payload or {},
                correlation_id=correlation_id,
            )
            candidate = request("admit", identity, f"{event_id}:admit")
            candidate["precondition"] = {
                "revision": checkpoint["revision"],
                "checkpoint_digest": checkpoint["execution_checkpoint_digest"],
            }
            candidate["arguments"] = {
                "ordered_deliveries": [
                    {
                        "delivery_mode": "input",
                        "envelope": envelope,
                        "envelope_digest": ds.delivery_request_digest(identity, "input", envelope),
                    }
                ]
            }
            with self.command_connection() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO commands VALUES (?,?,?,?,NULL)",
                    (event_id, command, self.endpoint, canonical_bytes(candidate)),
                )
                saved = connection.execute(
                    "SELECT command,admit,process,endpoint FROM commands WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if saved[0] != command:
                    raise ValueError("event ID reused for a different application command")
        command_client = PublicHostClient(
            self.journal, {"triage": EndpointBinding(saved[3], "triage")}, self.transport
        )
        candidate = json.loads(saved[1])
        admitted = checked(command_client.submit("triage", candidate))["checkpoint"]
        if saved[2] is None:
            aggregate = admitted["root_record"]["aggregate_state"]
            runtime = next(
                r for r in aggregate["runtimes"] if r["runtime_id"] == aggregate["root_runtime_id"]
            )
            process = request("process", identity, f"{event_id}:process")
            process["target"].update(
                runtime_id=runtime["runtime_id"], runtime_incarnation=runtime["identity_origin"]
            )
            process["precondition"] = {
                "revision": admitted["revision"],
                "checkpoint_digest": admitted["execution_checkpoint_digest"],
            }
            with self.command_connection() as connection:
                connection.execute(
                    "UPDATE commands SET process=? WHERE event_id=? AND process IS NULL",
                    (canonical_bytes(process), event_id),
                )
                saved_process = connection.execute(
                    "SELECT process FROM commands WHERE event_id=?", (event_id,)
                ).fetchone()[0]
        else:
            saved_process = saved[2]
        result = checked(command_client.submit("triage", json.loads(saved_process)))
        terminal = result.get("terminal_receipt")
        expected_digest = candidate["arguments"]["ordered_deliveries"][0]["envelope_digest"]
        if (
            terminal is None
            or terminal["event_id"] != event_id
            or terminal["request_digest"] != expected_digest
        ):
            raise ValueError("processing receipt does not identify this command")
        if result["core_result"]["disposition"] != "handled":
            raise ValueError("event was not handled; inspect the committed step receipt")
        return result["checkpoint"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "analyze", "read", "retry", "receipt"])
    parser.add_argument("identity", help="ticket ID, or saved operation ID for retry/receipt")
    parser.add_argument("--event-id", help="stable ID required for a new workflow command")
    parser.add_argument("--text", default="Customer support request")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8090/v1/operations")
    parser.add_argument("--journal", type=Path, default=ROOT / "var/client.sqlite3")
    args = parser.parse_args()
    app = TriageClient(args.journal, args.endpoint, os.environ["TRIAGE_TOKEN"])
    if args.command == "create":
        result = app.create(args.identity)
    elif args.command == "read":
        result = app.read(args.identity)
    elif args.command in {"retry", "receipt"}:
        result = getattr(app.client, args.command)(args.identity)
    else:
        if not args.event_id:
            parser.error("--event-id is required; preserve it across retries")
        payload = {"text": args.text, "request_id": f"{args.identity}:analysis"}
        result = app.event(args.identity, args.command, args.event_id, payload)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
