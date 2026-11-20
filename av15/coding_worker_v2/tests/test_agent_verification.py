"""Signed external-verifier handoff contract tests."""
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import verification  # noqa: E402


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class TestVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.private = self.root / "private.pem"
        self.public = self.root / "public.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA",
                        "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(self.private)], capture_output=True, check=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.private), "-pubout",
                        "-out", str(self.public)], capture_output=True, check=True)
        self.request_payload = {
            "schema": verification.SCHEMA, "task_id": "P-t1",
            "mission_id": "M", "plan_id": "P", "plan_digest": "a" * 64,
            "scope_digest": "sha256:" + "f" * 64,
            "allowed_paths": ["calc.py"],
            "source_sha": "b" * 40, "patch_digest": "c" * 64,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "receipt_version": 1, "receipt_sha256": "d" * 64,
            "lease_token_sha256": "e" * 64, "executor_id": "AV15",
            "executor_runner_id": "worker-1",
        }
        self.request = {
            "request": self.request_payload,
            "request_digest": hashlib.sha256(
                verification._canonical(self.request_payload)).hexdigest(),
        }
        self.request_path = self.root / "task.request.json"
        self.request_path.write_text(json.dumps(self.request), encoding="utf-8")
        self.proof_path = self.root / "task.proof.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_proof(self, **overrides):
        now = datetime.now(timezone.utc)
        payload = {
            **self.request_payload, "request_digest": self.request["request_digest"],
            "verifier_id": "independent-ci", "runner_id": "verifier-2",
            "status": "PASS", "evidence_refs": ["run:123"],
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
        }
        payload.update(overrides)
        signed = self.root / "payload.json"
        signature = self.root / "signature.bin"
        signed.write_bytes(verification._canonical(payload))
        subprocess.run(["openssl", "dgst", "-sha256", "-sign",
                        str(self.private), "-out", str(signature), str(signed)],
                       capture_output=True, check=True)
        self.proof_path.write_text(json.dumps({
            "payload": payload,
            "signature": base64.b64encode(signature.read_bytes()).decode("ascii"),
        }), encoding="utf-8")

    def test_signed_proof_reads_back_with_exact_bindings(self):
        self.write_proof()
        proof = verification.read_signed_proof(
            self.request_path, self.proof_path, self.public)
        self.assertTrue(proof["authenticated"])
        self.assertEqual(proof["patch_digest"], self.request_payload["patch_digest"])
        self.assertEqual(proof["receipt_sha256"],
                         self.request_payload["receipt_sha256"])

    def test_mismatched_source_or_patch_is_rejected(self):
        self.write_proof(patch_digest="f" * 64)
        with self.assertRaises(verification.VerificationError) as ctx:
            verification.read_signed_proof(
                self.request_path, self.proof_path, self.public)
        self.assertEqual(ctx.exception.reason, "VERIFIER_PROOF_MISMATCH")

    def test_executor_cannot_self_verify(self):
        self.write_proof(verifier_id="AV15")
        with self.assertRaises(verification.VerificationError):
            verification.read_signed_proof(
                self.request_path, self.proof_path, self.public)

    def test_expired_proof_is_rejected(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        self.write_proof(issued_at=old.isoformat(),
                         expires_at=(old + timedelta(minutes=5)).isoformat())
        with self.assertRaises(verification.VerificationError) as ctx:
            verification.read_signed_proof(
                self.request_path, self.proof_path, self.public)
        self.assertEqual(ctx.exception.reason, "VERIFIER_PROOF_STALE")

    def test_forged_signature_is_rejected(self):
        self.write_proof()
        proof = json.loads(self.proof_path.read_text(encoding="utf-8"))
        proof["payload"]["evidence_refs"] = ["forged"]
        self.proof_path.write_text(json.dumps(proof), encoding="utf-8")
        with self.assertRaises(verification.VerificationError) as ctx:
            verification.read_signed_proof(
                self.request_path, self.proof_path, self.public)
        self.assertEqual(ctx.exception.reason, "VERIFIER_SIGNATURE_INVALID")

    def test_oversized_request_is_rejected_before_json_decode(self):
        self.request_path.write_bytes(b"x" * (verification.MAX_BYTES + 1))
        with mock.patch.object(verification.json, "loads") as loads:
            with self.assertRaises(verification.VerificationError) as ctx:
                verification.read_signed_proof(
                    self.request_path, self.proof_path, self.public)
        self.assertEqual(ctx.exception.reason, "VERIFIER_PAYLOAD_TOO_LARGE")
        loads.assert_not_called()


if __name__ == "__main__":
    unittest.main()
