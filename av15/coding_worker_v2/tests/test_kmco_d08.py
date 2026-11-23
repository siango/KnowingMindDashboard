"""D08 negative and positive preflight contracts; no external origin is contacted."""
import hashlib
import unittest
from unittest.mock import patch

from av15.coding_worker_v2.agent import ingress
from av15.coding_worker_v2.agent.kmco_d08 import preflight_d08
from av15.coding_worker_v2.agent.tools import ToolError


class TestD08Preflight(unittest.TestCase):
    def setUp(self):
        self.envelope = {
            "command_id": "command-1", "idempotency_key": "", "principal_id": "owner",
            "channel": "CHATGPT", "mission_id": "mission", "task_id": "task",
            "intent": "STATUS", "scope": {"plan_id": "plan", "allowed_paths": ["."],
                                          "payload": {}},
            "approval_ref": "", "source_hash": "a"*40, "timestamp": 1,
            "correlation_id": "corr",
        }
        self.envelope["idempotency_key"] = ingress.idempotency_key(self.envelope)
        self.ctx = {
            "mission_id": "mission", "account_id": "account",
            "command_auth": {"channel": "CHATGPT", "principal_id": "owner",
                "mission_id": "mission", "account_id": "account", "task_ids": ["task"]},
            "command_channels": {"CHATGPT": {"principals": {"owner": {
                "account_id": "account", "mission_ids": ["mission"],
                "task_ids": ["task"]}}}},
        }
        self.refresh_digest()

    def refresh_digest(self):
        self.ctx["command_auth"]["body_sha256"] = ingress.digest(self.envelope)

    def test_unauthenticated_rejected(self):
        with patch.object(ingress, "qualified", return_value=False):
            with self.assertRaises(ToolError):
                preflight_d08(self.envelope, self.ctx)

    def test_authenticated_preflight_remains_nonexecuting(self):
        with patch.object(ingress, "qualified", return_value=True):
            result = preflight_d08(self.envelope, self.ctx)
        self.assertEqual(result["status"], "TRUSTED_INGRESS_PREFLIGHT_ONLY")
        self.assertFalse(result["dispatched"])
        self.assertFalse(result["provider_ack_sent"])
        self.assertFalse(result["transport_signature_reverified"])
        self.assertFalse(result["replay_ledger_checked"])
        self.assertNotIn("scope", result)

    def test_wrong_account_denied(self):
        self.ctx["command_auth"]["account_id"] = "other"
        with patch.object(ingress, "qualified", return_value=True):
            with self.assertRaises(ToolError):
                preflight_d08(self.envelope, self.ctx)

    def test_unbound_body_denied(self):
        self.envelope["scope"]["payload"]["x"] = "tampered"
        with patch.object(ingress, "qualified", return_value=True):
            with self.assertRaises(ToolError):
                preflight_d08(self.envelope, self.ctx)

    def test_invalid_semantic_key_denied(self):
        self.envelope["idempotency_key"] = "b"*64
        self.refresh_digest()
        with patch.object(ingress, "qualified", return_value=True):
            with self.assertRaises(ToolError):
                preflight_d08(self.envelope, self.ctx)

    def test_oversized_payload_denied(self):
        self.envelope["scope"]["payload"]["x"] = "z"*65000
        self.refresh_digest()
        with patch.object(ingress, "qualified", return_value=True):
            with self.assertRaises(ToolError):
                preflight_d08(self.envelope, self.ctx)


if __name__ == "__main__":
    unittest.main()
