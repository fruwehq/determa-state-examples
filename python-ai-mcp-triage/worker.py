"""AI analysis through MCP after remote commit, with durable local outcomes."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from determa.state.wire import canonical_bytes, decoded_typed_value

from client import TriageClient
from model_client import analyze

ROOT = Path(__file__).resolve().parent


class TriageWorker:
    def __init__(self, path: Path, app: TriageClient, builder=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.app = app
        self.builder = builder or self.invoke_model
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS outcomes "
                "(effect_id TEXT PRIMARY KEY, intent BLOB NOT NULL, "
                "result_bytes BLOB NOT NULL, outcome BLOB NOT NULL, reported INTEGER NOT NULL)"
            )
            connection.execute("CREATE TABLE IF NOT EXISTS destination (endpoint TEXT NOT NULL)")
            rows = connection.execute("SELECT endpoint FROM destination").fetchall()
            if not rows:
                connection.execute("INSERT INTO destination VALUES (?)", (app.endpoint,))
            elif rows != [(app.endpoint,)]:
                raise ValueError("worker destination cannot change for retained work")

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def invoke_model(payload):
        return canonical_bytes(analyze(payload["text"]))

    def drain(self, identity):
        with self.connect() as connection:
            if connection.execute("SELECT endpoint FROM destination").fetchall() != [
                (self.app.endpoint,)
            ]:
                raise ValueError("worker destination cannot change for retained work")
        checkpoint = self.app.read(identity)
        completed = 0
        for item in checkpoint["pending_outbox_intents"]:
            intent = item["intent"]
            if intent["event"] != "analysis_requested":
                raise ValueError("unregistered native handler event")
            raw = canonical_bytes(intent)
            effect_id = intent["effect_id"]
            with self.connect() as connection:
                saved = connection.execute(
                    "SELECT intent,outcome,reported FROM outcomes WHERE effect_id=?", (effect_id,)
                ).fetchone()
            if saved is not None and saved[0] != raw:
                raise ValueError("conflicting retained effect identity")
            if saved is None:
                result_bytes = self.builder(decoded_typed_value(intent["payload"]))
                outcome = json.loads(result_bytes)
                with self.connect() as connection:
                    connection.execute(
                        "INSERT INTO outcomes VALUES (?,?,?,?,0)",
                        (effect_id, raw, result_bytes, canonical_bytes(outcome)),
                    )
            elif saved[2]:
                continue
            else:
                outcome = json.loads(saved[1])
            # Local result is durable before remote admission/processing; a timeout
            # is reconciled by the application's saved complete command phases.
            self.app.event(
                identity,
                "analysis_ready",
                f"result:{effect_id}",
                outcome,
                correlation_id=intent["correlation_id"],
            )
            with self.connect() as connection:
                connection.execute("UPDATE outcomes SET reported=1 WHERE effect_id=?", (effect_id,))
            completed += 1
        return completed

    def export(self, effect_id, path):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT result_bytes FROM outcomes WHERE effect_id=?", (effect_id,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown artifact")
        path.write_bytes(row[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["drain", "export"])
    parser.add_argument("identity", help="workflow ID for drain, effect ID for export")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8090/v1/operations")
    parser.add_argument("--database", type=Path, default=ROOT / "var/worker.sqlite3")
    parser.add_argument("--journal", type=Path, default=ROOT / "var/worker-client.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "var/analysis.json")
    args = parser.parse_args()
    app = TriageClient(args.journal, args.endpoint, os.environ["TRIAGE_TOKEN"])
    worker = TriageWorker(args.database, app)
    if args.command == "drain":
        print(f"reported={worker.drain(args.identity)}")
    else:
        worker.export(args.identity, args.output)
        print(args.output)


if __name__ == "__main__":
    main()
