"""Customer-native archive work after remote commit, with durable local outcomes."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import zipfile
from pathlib import Path

from determa.state.wire import canonical_bytes, decoded_typed_value

from client import DocumentClient

ROOT = Path(__file__).resolve().parent


class DocumentWorker:
    def __init__(self, path: Path, app: DocumentClient, builder=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.app = app
        self.builder = builder or self.build_archive
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS outcomes "
                "(effect_id TEXT PRIMARY KEY, intent BLOB NOT NULL, "
                "archive BLOB NOT NULL, outcome BLOB NOT NULL, reported INTEGER NOT NULL)"
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
    def build_archive(payload):
        # Native BytesIO/ZipFile/bytes stay local; only declared JSON results cross HTTP.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            item = zipfile.ZipInfo("document.txt", date_time=(2020, 1, 1, 0, 0, 0))
            archive.writestr(item, payload["text"].encode())
        return stream.getvalue()

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
            if intent["event"] != "document_requested":
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
                archive = self.builder(decoded_typed_value(intent["payload"]))
                outcome = {
                    "artifact_digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
                    "byte_count": len(archive),
                }
                with self.connect() as connection:
                    connection.execute(
                        "INSERT INTO outcomes VALUES (?,?,?,?,0)",
                        (effect_id, raw, archive, canonical_bytes(outcome)),
                    )
            elif saved[2]:
                continue
            else:
                outcome = json.loads(saved[1])
            # Local result is durable before remote admission/processing; a timeout
            # is reconciled by the application's saved complete command phases.
            self.app.event(
                identity,
                "document_ready",
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
                "SELECT archive FROM outcomes WHERE effect_id=?", (effect_id,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown artifact")
        path.write_bytes(row[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["drain", "export"])
    parser.add_argument("identity", help="workflow ID for drain, effect ID for export")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8089/v1/operations")
    parser.add_argument("--database", type=Path, default=ROOT / "var/worker.sqlite3")
    parser.add_argument("--journal", type=Path, default=ROOT / "var/worker-client.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "var/document.zip")
    args = parser.parse_args()
    app = DocumentClient(args.journal, args.endpoint, os.environ["DOCUMENT_TOKEN"])
    worker = DocumentWorker(args.database, app)
    if args.command == "drain":
        print(f"reported={worker.drain(args.identity)}")
    else:
        worker.export(args.identity, args.output)
        print(args.output)


if __name__ == "__main__":
    main()
