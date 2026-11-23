"""Ten canonical batch-01 MicroOps: actual stage checks + fail-closed tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from coding_worker_v2.agent import ingress
from coding_worker_v2.agent import kmco_batch01 as batch
from coding_worker_v2.agent.tools import ToolError


class Batch01StageTests(unittest.TestCase):
    def setUp(self):
        self.envelope = {"command_id": "c1", "idempotency_key": "", "principal_id": "owner",
            "channel": "CHATGPT", "mission_id": "mission", "task_id": "task",
            "intent": "STATUS", "scope": {"plan_id": "plan", "allowed_paths": ["."], "payload": {}},
            "approval_ref": "", "source_hash": "a"*40, "timestamp": 1, "correlation_id": "corr"}
        self.envelope["idempotency_key"] = ingress.idempotency_key(self.envelope)
        self.ctx = {
            "mission_id": "mission", "task_id": "task", "account_id": "account",
            "command_auth": {"channel": "CHATGPT", "principal_id": "owner", "method": "MCP_OAUTH",
                "mission_id": "mission", "account_id": "account", "task_ids": ["task"],
                "permissions": ["kmcw.mission.status"]},
            "command_channels": {"CHATGPT": {"principals": {"owner": {
                "account_id": "account", "mission_ids": ["mission"], "task_ids": ["task"]}}}}}
        self.ctx["command_auth"]["body_sha256"] = ingress.digest(self.envelope)
        self.output = {"mission_id": "mission", "task_id": "task", "account_id": "account",
                       "visibility": "PRIVATE_OWNER", "evidence_refs": ["receipt:one"],
                       "payload": {"token": "SECRET-NEVER-FORWARD", "status": "approved"},
                       "delivery_channel": "CHATGPT"}
        self.egress = {"mission_id": "mission", "task_id": "task", "account_id": "account",
                       "read_only": True, "egress_owner_authenticated": True,
                       "egress_consent": {"verified": True, "mission_id": "mission",
                           "task_id": "task", "account_id": "account",
                           "channels": ["CHATGPT"], "evidence_ref": "consent:1"},
                       "verified_evidence_refs": frozenset({"receipt:one"})}

    def test_01_transport_middleware_auth_does_not_fake_signature(self):
        with patch.object(ingress, "qualified", return_value=True):
            out = batch.d08_m01_verified_middleware(self.ctx)
        self.assertFalse(out["transport_signature_independently_verified"])
        with patch.object(ingress, "qualified", return_value=False):
            with self.assertRaises(ToolError):
                batch.d08_m01_verified_middleware(self.ctx)

    def test_02_canonical_bounded_json_only(self):
        wire = json.dumps(self.envelope, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode()
        self.assertEqual(batch.d08_m02_parse_bounded(wire), self.envelope)
        with self.assertRaises(ToolError): batch.d08_m02_parse_bounded(wire + b" ")
        with self.assertRaises(ToolError): batch.d08_m02_parse_bounded(b"x" * 64001)
        with self.assertRaises(ToolError): batch.d08_m02_parse_bounded(b"{}")

    def test_03_principal_and_body_scope(self):
        with patch.object(ingress, "qualified", return_value=True):
            self.assertTrue(batch.d08_m03_validate(self.envelope, self.ctx)["identity_scoped"])
            tampered = dict(self.envelope, task_id="other")
            with self.assertRaises(ToolError): batch.d08_m03_validate(tampered, self.ctx)

    def test_04_dedup_reads_original_checkpoint_and_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            store = Mock()
            store.checkpoints = Path(d)
            self.ctx["store"] = store
            store.load_checkpoint.return_value = None
            with patch.object(ingress, "qualified", return_value=True):
                result = batch.d08_m04_dedup_read(self.envelope, self.ctx)
                self.assertFalse(result["checkpoint_created"])
                self.assertFalse(result["existing_checkpoint"])
                store.load_checkpoint.return_value = {
                    "data": {"semantic_sha256": self.envelope["idempotency_key"]}}
                self.assertTrue(batch.d08_m04_dedup_read(self.envelope, self.ctx)["existing_checkpoint"])
                store.load_checkpoint.return_value = {"data": {"semantic_sha256": "b"*64}}
                with self.assertRaises(ToolError):
                    batch.d08_m04_dedup_read(self.envelope, self.ctx)

    def test_05_exact_existing_route_no_dispatch(self):
        registry = Mock()
        registry.names.return_value = ["kmcw.mission.status"]
        with patch.object(ingress, "qualified", return_value=True):
            out = batch.d08_m05_resolve("kmcw.mission.status", self.envelope, self.ctx, registry)
            self.assertFalse(out["dispatched"])
            with self.assertRaises(ToolError):
                batch.d08_m05_resolve("kmcw.mission.control", self.envelope, self.ctx, registry)

    def test_06_original_handler_result_without_provider_ack(self):
        registry = Mock()
        registry.names.return_value = ["kmcw.mission.status"]
        registry.call.return_value = {"status": "CURRENT_READBACK"}
        with patch.object(ingress, "qualified", return_value=True):
            out = batch.d08_m06_existing_readonly(
                "kmcw.mission.status", self.envelope, self.ctx, registry)
        self.assertTrue(out["existing_handler_called"])
        self.assertFalse(out["provider_ack_verified"])
        registry.call.assert_called_once_with("kmcw.mission.status",
                                               {"command": self.envelope}, self.ctx)

    def test_07_output_schema_and_scope(self):
        out = batch.d09_m01_schema(self.output, self.egress)
        self.assertEqual(out["operation_id"], "D09-M01")
        self.output["account_id"] = "other"
        with self.assertRaises(ToolError):
            batch.d09_m01_schema(self.output, self.egress)

    def test_08_consent_evidence_is_mandatory(self):
        out = batch.d09_m02_acl_consent(self.output, self.egress)
        self.assertTrue(out["owner_only"])
        self.egress["egress_consent"]["verified"] = False
        with self.assertRaises(ToolError):
            batch.d09_m02_acl_consent(self.output, self.egress)

    def test_09_does_not_forward_secrets_even_in_safe_meta(self):
        out = batch.d09_m03_redact(self.output, self.egress)
        self.assertFalse(out["content_forwarded"])
        self.assertNotIn("SECRET-NEVER-FORWARD", str(out))
        self.assertNotIn("approved", str(out))
        self.output["payload"]["status"] = "x" * 161
        with self.assertRaises(ToolError):
            batch.d09_m03_redact(self.output, self.egress)

    def test_10_evidence_ref_requires_trusted_source(self):
        out = batch.d09_m04_evidence(self.output, self.egress)
        self.assertEqual(out["evidence_refs"], ["receipt:one"])
        self.assertFalse(out["durable_readback"])
        self.egress["verified_evidence_refs"] = frozenset()
        with self.assertRaises(ToolError):
            batch.d09_m04_evidence(self.output, self.egress)


if __name__ == "__main__":
    unittest.main()
