from __future__ import annotations

import copy
import os
import signal
import tempfile
import unittest
from pathlib import Path

from infrastructure_lifecycle.client import ProviderProcess, ProviderTransportError
from infrastructure_lifecycle.provider import ProviderError

REQUEST = {
    "operation": "ensure_resource",
    "scope_identity": "local-infra",
    "effect_id": "effect-A",
    "resource_id": "server-1",
    "expected_revision": None,
    "desired": {"present": True, "size": "small"},
}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "destination.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_original_response_and_native_inspection_survive_separate_process_restart(
        self,
    ):
        with ProviderProcess(self.path, "local-infra") as client:
            first = client.execute(REQUEST)
            self.assertTrue(client.verify_retained(REQUEST, first))
            client.execute(
                {**REQUEST, "effect_id": "effect-B", "expected_revision": "0"}
            )
        with ProviderProcess(self.path, "local-infra") as client:
            self.assertEqual(client.execute(REQUEST), first)
            self.assertTrue(client.verify_retained(REQUEST, first))
            self.assertFalse(
                client.verify_retained(REQUEST, {**first, "status": "invented"})
            )
            self.assertEqual(client.inspect("missing"), {"status": "unknown"})
            with self.assertRaisesRegex(ProviderError, "operation_conflict"):
                client.execute({**REQUEST, "desired": {"present": False}})

    def test_scope_authorization_precedes_transport_and_native_disclosure(self):
        with ProviderProcess(self.path, "local-infra") as client:
            client.execute(REQUEST)
            with self.assertRaisesRegex(ProviderError, "unauthorized_scope"):
                client.execute({**REQUEST, "scope_identity": "another"})
            with self.assertRaisesRegex(ProviderError, "unauthorized_scope"):
                client.verify_retained({**REQUEST, "scope_identity": "another"}, {})
            self.assertEqual(client.execute(REQUEST)["resource"]["revision"], "0")

    def test_native_verification_keeps_boolean_and_integer_values_distinct(self):
        with ProviderProcess(self.path, "local-infra") as client:
            first = client.execute(REQUEST)
            changed_request = copy.deepcopy(REQUEST)
            changed_request["desired"]["present"] = 1
            self.assertFalse(client.verify_retained(changed_request, first))
            changed_response = copy.deepcopy(first)
            changed_response["resource"]["desired"]["present"] = 1
            self.assertFalse(client.verify_retained(REQUEST, changed_response))
            self.assertTrue(client.verify_retained(REQUEST, first))

    def test_dead_transport_never_retries_or_resets_destination(self):
        with ProviderProcess(self.path, "local-infra") as client:
            first = client.execute(REQUEST)
            client._process.kill()
            client._process.wait(timeout=5)
            with self.assertRaisesRegex(ProviderTransportError, "delivery_ambiguous"):
                client.execute(REQUEST)
            with self.assertRaisesRegex(ProviderTransportError, "delivery_ambiguous"):
                client.inspect("effect-A")
        with ProviderProcess(self.path, "local-infra") as client:
            self.assertTrue(client.verify_retained(REQUEST, first))
            self.assertEqual(client.execute(REQUEST), first)

    def test_stopped_destination_times_out_without_hidden_restart_or_retry(self):
        with ProviderProcess(self.path, "local-infra", timeout=0.2) as client:
            first = client.execute(REQUEST)
            os.kill(client._process.pid, signal.SIGSTOP)
            with self.assertRaisesRegex(ProviderTransportError, "delivery_ambiguous"):
                client.execute(
                    {**REQUEST, "effect_id": "effect-B", "expected_revision": "0"}
                )
            self.assertIsNotNone(client._process.returncode)
        with ProviderProcess(self.path, "local-infra") as client:
            self.assertEqual(client.inspect("effect-B"), {"status": "unknown"})
            self.assertTrue(client.verify_retained(REQUEST, first))


if __name__ == "__main__":
    unittest.main()
