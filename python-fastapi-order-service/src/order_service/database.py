from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    lifecycle_status TEXT NOT NULL,
    definition_fingerprint TEXT NOT NULL,
    aggregate_bytes BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inbox (
    event_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS outbox (
    effect_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_name TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT,
    UNIQUE (order_id, sequence),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS effect_deliveries (
    effect_id TEXT PRIMARY KEY,
    adapter_name TEXT NOT NULL,
    delivered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (effect_id) REFERENCES outbox(effect_id)
);

CREATE TABLE IF NOT EXISTS migration_audits (
    order_id TEXT NOT NULL,
    migration_sequence TEXT NOT NULL,
    descriptor_digest TEXT NOT NULL,
    audit_json TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (order_id, migration_sequence),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.initialize()
