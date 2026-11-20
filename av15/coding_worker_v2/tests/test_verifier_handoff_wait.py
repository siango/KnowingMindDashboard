"""Transport timing regressions; these create no keys or signed proofs."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from coding_worker_v2.agent import verification as v


class TestHandoffWait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / "public.pem"
        self.key.touch()  # presence only; cryptographic reader mocked below
        self.request = self.root / "request.json"
        self.proof = self.root / "proof.json"

    def wait(self, lease=lambda: True):
        return v.wait_signed_proof(self.request, self.proof, self.key, lease)

    @mock.patch.dict(os.environ, {"KMW2_VERIFIER_WAIT_S": "1"})
    def test_delayed_publication_reaches_original_signature_reader(self):
        timer = threading.Timer(0.05, self.proof.touch)
        timer.start()
        try:
            with mock.patch.object(v, "read_signed_proof", return_value={"checked": True}) as reader:
                self.assertEqual(self.wait(), {"checked": True})
                reader.assert_called_once_with(self.request, self.proof, self.key)
        finally:
            timer.join()

    @mock.patch.dict(os.environ, {"KMW2_VERIFIER_WAIT_S": "0"})
    def test_timeout_does_not_read_or_accept_missing_proof(self):
        with mock.patch.object(v, "read_signed_proof") as reader:
            with self.assertRaises(v.VerificationError) as exc:
                self.wait()
            self.assertEqual(exc.exception.reason, "VERIFIER_PROOF_UNAVAILABLE")
            reader.assert_not_called()

    def test_lost_lease_stops_before_readback(self):
        self.proof.touch()
        with mock.patch.object(v, "read_signed_proof") as reader:
            with self.assertRaises(v.VerificationError) as exc:
                self.wait(lambda: False)
            self.assertEqual(exc.exception.reason, "VERIFIER_LEASE_LOST")
            reader.assert_not_called()

    def test_invalid_published_proof_is_not_retried(self):
        self.proof.touch()
        with mock.patch.object(v, "read_signed_proof", side_effect=v.VerificationError("VERIFIER_SIGNATURE_INVALID")) as reader:
            with self.assertRaises(v.VerificationError):
                self.wait()
            self.assertEqual(reader.call_count, 1)

    def test_invalid_wait_configuration_fails_closed(self):
        for value in ["nan", "inf", "-1", "301", "bad"]:
            with self.subTest(value=value), mock.patch.dict(os.environ, {"KMW2_VERIFIER_WAIT_S": value}):
                with self.assertRaises(v.VerificationError) as exc:
                    self.wait()
                self.assertEqual(exc.exception.reason, "VERIFIER_WAIT_INVALID")

    def test_missing_authority_does_not_wait(self):
        self.key.unlink()
        with self.assertRaises(v.VerificationError) as exc:
            self.wait()
        self.assertEqual(exc.exception.reason, "VERIFIER_AUTHORITY_UNAVAILABLE")
