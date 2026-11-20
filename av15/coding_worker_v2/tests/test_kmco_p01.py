"""P01 real file IO positive and negative QC; no mocked handlers."""
import tempfile
import unittest
from pathlib import Path
from coding_worker_v2.agent.kmco_p01 import (
    run_p01, source_locator, evidence_lookup, provenance_checker,
    contradiction_detector, answer_gate, P01Blocked,
)


class P01RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "real.txt").write_text("alpha\\nbeta evidence\\ngamma\\n", encoding="utf-8")

    def test_real_five_step_positive(self):
        result = run_p01(self.root, "real.txt", "beta evidence")
        self.assertEqual(result["steps_completed"], [
            "P01-M01", "P01-M02", "P01-M03", "P01-M04", "P01-M05"])
        self.assertEqual(result["output"]["citation"]["line"], 2)
        self.assertEqual(result["output"]["text"], "beta evidence")
        self.assertFalse(result["output"]["semantic_truth_verified"])

    def test_missing_quote_denied(self):
        with self.assertRaisesRegex(P01Blocked, "P01_QUOTE_NOT_FOUND"):
            run_p01(self.root, "real.txt", "invented quote")

    def test_escape_denied(self):
        with self.assertRaisesRegex(P01Blocked, "P01_SOURCE_SCOPE_DENIED"):
            source_locator(self.root, "../secret.txt")

    def test_tampering_denied(self):
        pinned = source_locator(self.root, "real.txt")
        evidence = evidence_lookup(self.root, pinned, "beta evidence")
        (self.root / "real.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(P01Blocked, "P01_SOURCE_CHANGED"):
            provenance_checker(self.root, evidence)

    def test_structured_conflict_denied(self):
        a = [{"subject": "s", "predicate": "p", "value": "one"},
             {"subject": "s", "predicate": "p", "value": "two"}]
        with self.assertRaisesRegex(P01Blocked, "P01_STRUCTURED_CONTRADICTION"):
            contradiction_detector(a)

    def test_symlink_denied(self):
        (self.root / "link.txt").symlink_to(self.root / "real.txt")
        with self.assertRaisesRegex(P01Blocked, "P01_SOURCE_SYMLINK_DENIED"):
            source_locator(self.root, "link.txt")


if __name__ == "__main__":
    unittest.main()
