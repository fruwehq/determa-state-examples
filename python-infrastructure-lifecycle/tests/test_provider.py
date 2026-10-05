from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from infrastructure_lifecycle.provider import DurableProvider, ProviderError, canonical

REQUEST = {
    "operation": "ensure_resource",
    "scope_identity": "local-infra",
    "effect_id": "effect-A",
    "resource_id": "server-1",
    "expected_revision": None,
    "desired": {"present": True, "size": "small"},
}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "provider.sqlite"
        self.provider = DurableProvider(self.path, "local-infra")
        self.provider.setup()

    def tearDown(self):
        self.provider.close()
        self.temp.cleanup()

    def test_retained_exact_response_and_revision_conflicts_after_restart(self):
        first = self.provider.execute(REQUEST)
        next_request = {
            **REQUEST,
            "effect_id": "effect-B",
            "expected_revision": "0",
            "desired": {"present": False},
        }
        self.assertEqual(
            self.provider.execute(next_request)["resource"]["revision"], "1"
        )
        self.provider.close()
        self.provider = DurableProvider(self.path, "local-infra")
        self.provider.setup()
        self.assertEqual(self.provider.execute(REQUEST), first)
        with self.assertRaisesRegex(ProviderError, "operation_conflict"):
            self.provider.execute({**REQUEST, "desired": {"present": False}})
        with self.assertRaisesRegex(ProviderError, "resource_revision_conflict"):
            self.provider.execute({**REQUEST, "effect_id": "effect-C"})
        self.assertEqual(
            self.provider.connection.execute(
                "SELECT COUNT(*) FROM provider_operations"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.provider.execute(
                {
                    "operation": "inspect_operation",
                    "scope_identity": "local-infra",
                    "effect_id": "effect-A",
                }
            ),
            {"status": "committed", "request": REQUEST, "response": first},
        )

    def test_configured_scope_precedes_retained_disclosure_and_unknown_is_not_rollback(
        self,
    ):
        self.provider.execute(REQUEST)
        with self.assertRaisesRegex(ProviderError, "unauthorized_scope"):
            self.provider.execute(
                {
                    "operation": "inspect_operation",
                    "scope_identity": "other",
                    "effect_id": "effect-A",
                }
            )
        self.assertEqual(
            self.provider.execute(
                {
                    "operation": "inspect_operation",
                    "scope_identity": "local-infra",
                    "effect_id": "missing",
                }
            ),
            {"status": "unknown"},
        )

    def test_native_evidence_is_immutable_and_resource_corruption_refuses_without_repair(
        self,
    ):
        self.provider.execute(REQUEST)
        for table in ("provider_operations", "provider_origin"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.provider.connection.execute(f"DELETE FROM {table}")
        self.provider.connection.execute(
            "UPDATE provider_resources SET document=?", (b"{}",)
        )
        before = self.provider.connection.execute(
            "SELECT document FROM provider_resources"
        ).fetchone()[0]
        with self.assertRaisesRegex(ProviderError, "provider_evidence_mismatch"):
            self.provider.setup()
        with self.assertRaisesRegex(ProviderError, "provider_evidence_mismatch"):
            self.provider.execute(
                {**REQUEST, "effect_id": "effect-B", "expected_revision": "0"}
            )
        self.assertEqual(
            self.provider.connection.execute(
                "SELECT document FROM provider_resources"
            ).fetchone()[0],
            before,
        )

    def test_malformed_native_request_preserves_resource_and_operation_journal(self):
        for desired in (1.5, 2**63, {"nested": [float("nan")]}):
            with self.assertRaisesRegex(ProviderError, "invalid_request"):
                self.provider.execute({**REQUEST, "desired": desired})
        with self.assertRaisesRegex(ProviderError, "invalid_request"):
            self.provider.execute({**REQUEST, "resource_id": "\ud800"})
        for table in ("provider_resources", "provider_operations"):
            self.assertEqual(
                self.provider.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0],
                0,
            )

    def test_json_lines_process_roundtrip_and_duplicate_keys_refusal(self):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "infrastructure_lifecycle.provider",
                "--database",
                str(self.path),
                "--scope",
                "local-infra",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = (
            canonical(REQUEST)
            + b'\n{"operation":"read_resource","operation":"read_resource"}\n'
        )
        output, error = process.communicate(payload, timeout=15)
        self.assertEqual(process.returncode, 0, error.decode())
        first, second = map(json.loads, output.splitlines())
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second, {"status": "refused", "code": "invalid_request"})

    @unittest.skipUnless(os.name == "posix", "SIGKILL requires POSIX")
    def test_sigkill_before_and_after_provider_commit_then_native_receipt_reconciliation(
        self,
    ):
        bootstrap = """
import json, sys, time
from pathlib import Path
from infrastructure_lifecycle import provider
path, marker, cut, request = sys.argv[1:]
def observe(phase):
    if phase == cut:
        with open(marker, 'wb') as file:
            file.write(b'cut reached'); file.flush()
            import os; os.fsync(file.fileno())
        while True: time.sleep(60)
provider._observe = observe
instance = provider.DurableProvider(Path(path), 'local-infra')
instance.setup()
print(json.dumps(instance.execute(json.loads(request))), flush=True)
"""
        for cut in ("staged", "committed"):
            with self.subTest(cut=cut):
                path = Path(self.temp.name) / f"{cut}.sqlite"
                marker = path.with_suffix(".cut")
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-c",
                        bootstrap,
                        str(path),
                        str(marker),
                        cut,
                        canonical(REQUEST).decode(),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    deadline = time.monotonic() + 15
                    while not marker.exists():
                        if child.poll() is not None:
                            _, error = child.communicate()
                            self.fail(f"provider exited before {cut}: {error.decode()}")
                        if time.monotonic() >= deadline:
                            self.fail(f"provider did not reach {cut}")
                        time.sleep(0.01)
                    child.kill()
                    output, error = child.communicate(timeout=5)
                    self.assertEqual(child.returncode, -signal.SIGKILL, error.decode())
                    self.assertEqual(
                        output, b"", "no response may precede the chosen cut"
                    )
                    reopened = DurableProvider(path, "local-infra")
                    try:
                        reopened.setup()
                        evidence = reopened.execute(
                            {
                                "operation": "inspect_operation",
                                "scope_identity": "local-infra",
                                "effect_id": "effect-A",
                            }
                        )
                        self.assertEqual(
                            evidence["status"],
                            "unknown" if cut == "staged" else "committed",
                        )
                        count = reopened.connection.execute(
                            "SELECT COUNT(*) FROM provider_operations"
                        ).fetchone()[0]
                        self.assertEqual(count, 0 if cut == "staged" else 1)
                        applied = reopened.execute(REQUEST)
                        self.assertEqual(applied["resource"]["revision"], "0")
                        if cut == "committed":
                            self.assertEqual(applied, evidence["response"])
                        self.assertEqual(reopened.execute(REQUEST), applied)
                        self.assertEqual(
                            reopened.connection.execute(
                                "SELECT COUNT(*) FROM provider_operations"
                            ).fetchone()[0],
                            1,
                        )
                    finally:
                        reopened.close()
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
