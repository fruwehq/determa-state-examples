from __future__ import annotations

import itertools
import unittest
from pathlib import Path

import determa.state as ds
from determa.state.wire import decoded_typed_value

import infrastructure_lifecycle


class MachineTests(unittest.TestCase):
    def setUp(self):
        machine = Path(infrastructure_lifecycle.__file__).with_name("machine.yaml")
        self.bundle = ds.load_bundle(machine.read_text())
        self.resolver = ds.MemoryArtifactResolver(
            definitions={self.bundle.fingerprint: self.bundle}
        )
        self.state = ds.create(
            self.bundle,
            machine_id="server",
            root_instance_id="server-1",
            creation_id="server-create",
            bindings={},
        )["state"]
        self.sequence = itertools.count()

    def process(self, event, **payload):
        envelope = ds.portable_envelope(
            event,
            f"input-{next(self.sequence)}",
            {
                "root": {
                    "root_instance_id": "server-1",
                    "root_runtime_id": self.state["root_runtime_id"],
                }
            },
            payload,
        )
        admitted = ds.admit(
            self.state,
            [
                {
                    "delivery_mode": "input",
                    "envelope": envelope,
                    "envelope_digest": ds.delivery_request_digest(
                        "server-1", "input", envelope
                    ),
                }
            ],
            self.resolver,
        )
        self.assertEqual(admitted["result"], "accepted")
        result = ds.step(
            admitted["state"], self.state["root_runtime_id"], self.resolver
        )
        self.state = result["state"]
        return result

    def values(self):
        return {
            item["variable_declaration_pointer"].rsplit("/", 1)[1]: decoded_typed_value(
                item["value"]
            )
            for item in self.state["runtimes"][0]["variables"]
        }

    def request(self, token="op-A", size="small"):
        self.process("set_desired", present=True, size=size)
        result = self.process("reconcile", operation_token=token)
        self.assertEqual(len(result["emissions"]), 1)
        return decoded_typed_value(result["emissions"][0]["payload"])

    def applied(self, token="op-A", revision="0", size="small"):
        return self.process(
            "resource_applied",
            operation_token=token,
            revision=revision,
            present=True,
            size=size,
        )

    def test_desired_and_observed_are_separate_and_request_pins_snapshot(self):
        requested = self.request()
        self.assertEqual(
            requested,
            {
                "resource_id": "server-1",
                "expected_revision": "",
                "present": True,
                "size": "small",
                "operation_token": "op-A",
            },
        )
        self.assertTrue(self.values()["desired_present"])
        self.assertFalse(self.values()["observed_known"])
        self.assertTrue(self.values()["pending"])
        self.process("set_desired", present=True, size="large")
        self.assertEqual(self.values()["requested_size"], "small")
        self.applied()
        self.assertEqual(self.values()["observed_size"], "small")
        self.assertEqual(self.values()["desired_size"], "large")
        self.assertFalse(self.values()["pending"])
        next_request = self.process("reconcile", operation_token="op-B")["emissions"]
        self.assertEqual(
            decoded_typed_value(next_request[0]["payload"])["expected_revision"], "0"
        )
        self.assertEqual(
            decoded_typed_value(next_request[0]["payload"])["size"], "large"
        )

    def test_stale_token_and_changed_original_request_cannot_update_observed(self):
        self.request()
        before = self.values()
        self.applied(token="old-operation")
        self.assertEqual(self.values(), before)
        self.applied(size="large")
        self.assertEqual(self.values(), before)
        self.applied()
        self.assertTrue(self.values()["observed_known"])

    def test_uncertainty_retains_pending_work_without_starting_another_attempt(self):
        self.request()
        self.process("resource_unknown", operation_token="op-A")
        before = self.values()
        self.assertEqual(
            self.process("reconcile", operation_token="op-B")["emissions"], []
        )
        self.assertEqual(self.values(), before)
        self.applied()
        self.assertFalse(self.values()["pending"])

    def test_domain_rejection_preserves_observed_and_requires_explicit_new_work(self):
        self.request()
        self.process("resource_rejected", operation_token="op-A")
        self.assertFalse(self.values()["pending"])
        self.assertFalse(self.values()["observed_known"])
        self.assertTrue(self.values()["desired_present"])
        self.assertEqual(
            self.process("reconcile", operation_token="op-A")["emissions"], []
        )
        self.assertEqual(
            len(self.process("reconcile", operation_token="op-B")["emissions"]), 1
        )

    def test_converged_resource_does_not_emit_another_operation(self):
        self.request()
        self.applied()
        self.assertEqual(
            self.process("reconcile", operation_token="op-B")["emissions"], []
        )
        self.assertFalse(self.values()["pending"])

    def test_removal_request_preserves_the_last_observed_revision(self):
        self.request()
        self.applied()
        self.process("set_desired", present=False, size="small")
        result = self.process("reconcile", operation_token="remove-A")
        removal = decoded_typed_value(result["emissions"][0]["payload"])
        self.assertFalse(removal["present"])
        self.assertEqual(removal["expected_revision"], "0")
        self.assertTrue(self.values()["observed_present"])
        self.process(
            "resource_applied",
            operation_token="remove-A",
            revision="1",
            present=False,
            size="small",
        )
        self.assertFalse(self.values()["observed_present"])
        self.assertEqual(self.values()["observed_revision"], "1")
        self.assertFalse(self.values()["pending"])

    def test_historical_identity_reuse_and_revision_rollback_are_rejected(self):
        self.request()
        self.applied()
        self.process("set_desired", present=False, size="small")
        self.process("reconcile", operation_token="remove-B")
        self.process(
            "resource_applied",
            operation_token="remove-B",
            revision="1",
            present=False,
            size="small",
        )
        self.process("set_desired", present=True, size="small")
        before = self.values()
        self.assertEqual(
            self.process("reconcile", operation_token="op-A")["emissions"], []
        )
        self.assertEqual(self.values(), before)
        self.process("reconcile", operation_token="op-C")
        before = self.values()
        self.applied(token="op-A", revision="0")
        self.assertEqual(self.values(), before)
        self.applied(token="op-C", revision="0")
        self.assertEqual(self.values(), before)
        self.applied(token="op-C", revision="2")
        self.assertEqual(self.values()["observed_revision"], "2")
        self.assertFalse(self.values()["pending"])
        self.assertEqual(
            self.values()["used_operation_tokens"], ["op-A", "remove-B", "op-C"]
        )


if __name__ == "__main__":
    unittest.main()
