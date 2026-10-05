"""Separate durable local provider, using scoped operation identities over JSON lines.

This example destination has its own protocol; it is not a Determa host or authority.
Only the process owner controls stdin. The configured scope is not taken from requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_TABLES = {
    "provider_origin": "CREATE TABLE provider_origin (singleton INTEGER PRIMARY KEY CHECK(singleton=1), scope TEXT NOT NULL, path TEXT NOT NULL)",
    "provider_resources": "CREATE TABLE provider_resources (resource_id TEXT PRIMARY KEY NOT NULL, revision TEXT NOT NULL, document BLOB NOT NULL)",
    "provider_operations": "CREATE TABLE provider_operations (sequence INTEGER PRIMARY KEY NOT NULL, effect_id TEXT UNIQUE NOT NULL, request BLOB NOT NULL, response BLOB NOT NULL)",
}
_TRIGGERS = {
    f"{table}_no_{action.lower()}": f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'provider_evidence_immutable'); END"
    for table in ("provider_origin", "provider_operations")
    for action in ("UPDATE", "DELETE")
}


class ProviderError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (ValueError, UnicodeError, TypeError) as error:
        raise ProviderError("invalid_request") from error


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderError("invalid_request")
        result[key] = value
    return result


def parse(source: bytes) -> Any:
    try:
        return json.loads(
            source.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ProviderError("invalid_request")
            ),
        )
    except (UnicodeError, ValueError) as error:
        raise ProviderError("invalid_request") from error


def _data(value: Any) -> bool:
    if value is None or type(value) in (str, bool):
        return True
    if type(value) is int:
        return -(2**63) <= value < 2**63
    if type(value) is list:
        return all(_data(item) for item in value)
    return type(value) is dict and all(
        type(key) is str and _data(item) for key, item in value.items()
    )


def _revision(value: Any) -> bool:
    return type(value) is str and (
        value == "0"
        or value.isascii()
        and value.isdecimal()
        and not value.startswith("0")
    )


def _observe(_phase: str) -> None:
    """Private transaction observation point; production has no crash command."""


class DurableProvider:
    def __init__(self, path: Path, scope: str) -> None:
        if not scope or str(path) == ":memory:":
            raise ProviderError("persistent_path_and_scope_required")
        self.path = path.resolve()
        self.scope = scope
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")

    def setup(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            count = self.connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            if count == 0:
                for sql in (*_TABLES.values(), *_TRIGGERS.values()):
                    self.connection.execute(sql)
                self.connection.execute(
                    "INSERT INTO provider_origin VALUES (1,?,?)",
                    (self.scope, str(self.path)),
                )
            # Existing evidence is validated, never filled in or repaired.
            self._validate()
            self.connection.commit()
        finally:
            self.connection.rollback()

    def _validate(self) -> None:
        if (
            self.connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
            or self.connection.execute("PRAGMA synchronous").fetchone()[0] != 2
        ):
            raise ProviderError("provider_unavailable")
        actual = dict(
            self.connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND (name LIKE 'provider_%' OR tbl_name LIKE 'provider_%')"
            )
        )
        if actual != {**_TABLES, **_TRIGGERS}:
            raise ProviderError("provider_evidence_mismatch")
        if self.connection.execute(
            "SELECT scope,path FROM provider_origin WHERE singleton=1"
        ).fetchone() != (self.scope, str(self.path)):
            raise ProviderError("provider_evidence_mismatch")
        last: dict[str, dict[str, Any]] = {}
        for expected, (sequence, effect_id, request_bytes, response_bytes) in enumerate(
            self.connection.execute(
                "SELECT sequence,effect_id,request,response FROM provider_operations ORDER BY sequence"
            ),
            start=1,
        ):
            request = parse(request_bytes)
            response = parse(response_bytes)
            if (
                sequence != expected
                or canonical(request) != request_bytes
                or canonical(response) != response_bytes
            ):
                raise ProviderError("provider_evidence_mismatch")
            self._request(request)
            resource_id = request["resource_id"]
            previous = last.get(resource_id)
            if (
                request["operation"] != "ensure_resource"
                or request["effect_id"] != effect_id
                or request["expected_revision"]
                != (previous["revision"] if previous else None)
            ):
                raise ProviderError("provider_evidence_mismatch")
            resource = {
                "scope_identity": self.scope,
                "resource_id": resource_id,
                "revision": str(int(previous["revision"]) + 1) if previous else "0",
                "desired": request["desired"],
            }
            if response != {
                "status": "applied",
                "effect_id": effect_id,
                "operation_digest": digest(request),
                "resource": resource,
            }:
                raise ProviderError("provider_evidence_mismatch")
            last[resource_id] = resource
        rows = self.connection.execute(
            "SELECT resource_id,revision,document FROM provider_resources"
        ).fetchall()
        if len(rows) != len(last):
            raise ProviderError("provider_evidence_mismatch")
        for resource_id, revision, raw in rows:
            if (
                resource_id not in last
                or revision != last[resource_id]["revision"]
                or raw != canonical(last[resource_id])
            ):
                raise ProviderError("provider_evidence_mismatch")

    def _request(self, request: Any) -> None:
        if type(request) is not dict or request.get("operation") not in (
            "ensure_resource",
            "inspect_operation",
            "read_resource",
        ):
            raise ProviderError("invalid_request")
        operation = request["operation"]
        required = {"operation", "scope_identity"} | (
            {"resource_id"} if operation == "read_resource" else {"effect_id"}
        )
        if operation == "ensure_resource":
            required |= {"resource_id", "expected_revision", "desired"}
        if set(request) != required or any(
            type(request[key]) is not str or not request[key]
            for key in required - {"desired", "expected_revision"}
        ):
            raise ProviderError("invalid_request")
        if request["scope_identity"] != self.scope:
            raise ProviderError("unauthorized_scope")
        if operation == "ensure_resource" and (
            not _data(request["desired"])
            or request["expected_revision"] is not None
            and not _revision(request["expected_revision"])
        ):
            raise ProviderError("invalid_request")

        canonical(request)  # Verify strict UTF-8 before any SQLite lookup.

    def execute(self, request: Any) -> dict[str, Any]:
        # Authenticate configured scope before existence or retained response lookup.
        self._request(request)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._validate()
            operation = request["operation"]
            if operation in ("inspect_operation", "ensure_resource"):
                receipt = self.connection.execute(
                    "SELECT request,response FROM provider_operations WHERE effect_id=?",
                    (request["effect_id"],),
                ).fetchone()
                if receipt:
                    if operation == "inspect_operation":
                        return {
                            "status": "committed",
                            "request": parse(receipt[0]),
                            "response": parse(receipt[1]),
                        }
                    if receipt[0] != canonical(request):
                        raise ProviderError("operation_conflict")
                    return parse(receipt[1])
                if operation == "inspect_operation":
                    # Absence is destination observation, not proof of host rollback.
                    return {"status": "unknown"}
            row = self.connection.execute(
                "SELECT revision,document FROM provider_resources WHERE resource_id=?",
                (request["resource_id"],),
            ).fetchone()
            if operation == "read_resource":
                return (
                    {"status": "found", "resource": parse(row[1])}
                    if row
                    else {"status": "absent"}
                )
            if request["expected_revision"] != (row[0] if row else None):
                raise ProviderError("resource_revision_conflict")
            resource = {
                "scope_identity": self.scope,
                "resource_id": request["resource_id"],
                "revision": str(int(row[0]) + 1) if row else "0",
                "desired": request["desired"],
            }
            response = {
                "status": "applied",
                "effect_id": request["effect_id"],
                "operation_digest": digest(request),
                "resource": resource,
            }
            self.connection.execute(
                "INSERT INTO provider_resources VALUES (?,?,?) ON CONFLICT(resource_id) DO UPDATE SET revision=excluded.revision,document=excluded.document",
                (request["resource_id"], resource["revision"], canonical(resource)),
            )
            sequence = (
                self.connection.execute(
                    "SELECT COUNT(*) FROM provider_operations"
                ).fetchone()[0]
                + 1
            )
            self.connection.execute(
                "INSERT INTO provider_operations VALUES (?,?,?,?)",
                (
                    sequence,
                    request["effect_id"],
                    canonical(request),
                    canonical(response),
                ),
            )
            _observe("staged")
            self.connection.commit()
            _observe("committed")
            return response
        finally:
            self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--scope", required=True)
    args = parser.parse_args()
    provider = DurableProvider(args.database, args.scope)
    try:
        provider.setup()
        for line in sys.stdin.buffer:
            try:
                result = provider.execute(parse(line))
            except ProviderError as error:
                result = {"status": "refused", "code": str(error)}
            print(canonical(result).decode(), flush=True)
    finally:
        provider.close()


if __name__ == "__main__":
    main()
