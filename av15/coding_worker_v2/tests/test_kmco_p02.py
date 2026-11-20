"""P02 real context continuity tests; no fabricated authority or receipts."""
import unittest
from coding_worker_v2.agent.kmco_p02 import run_p02, P02Blocked, context_fingerprint

BASE = {
    "mission_id": "G0-G1-MINIMUM-SELLABLE-DEMO",
    "task_id": "kmco-p02-manual",
    "scope": ["read-only"],
    "constraints": ["no production writes", "source first"],
    "source_sha": "a" * 40,
}


class P02RuntimeTests(unittest.TestCase):
    def test_positive_stable_resume(self):
        old = dict(BASE)
        new = dict(BASE, constraints=BASE["constraints"] + ["bounded retry"])
        output = run_p02(old, new)
        self.assertEqual(len(output["steps_completed"]), 5)
        self.assertEqual(output["changed_fields"], ["constraints"])
        self.assertEqual(output["context_sha256"], context_fingerprint(new))
        self.assertFalse(output["authority_granted"])
        self.assertFalse(output["execution_done"])

    def test_dropped_constraint_denied(self):
        with self.assertRaisesRegex(P02Blocked, "P02_CONSTRAINT_LOSS"):
            run_p02(BASE, dict(BASE, constraints=["source first"]))

    def test_scope_downgrade_denied(self):
        with self.assertRaisesRegex(P02Blocked, "P02_SCOPE_LOSS"):
            run_p02(BASE, dict(BASE, scope=["other"]))

    def test_mission_identity_change_denied(self):
        with self.assertRaisesRegex(P02Blocked, "P02_IDENTITY_OR_SOURCE_CHANGED"):
            run_p02(BASE, dict(BASE, mission_id="other"))

    def test_sha_change_denied(self):
        with self.assertRaisesRegex(P02Blocked, "P02_IDENTITY_OR_SOURCE_CHANGED"):
            run_p02(BASE, dict(BASE, source_sha="b" * 40))

    def test_bad_id_denied(self):
        with self.assertRaisesRegex(P02Blocked, "P02_ID_INVALID"):
            run_p02(BASE, dict(BASE, task_id="../escape"))


if __name__ == "__main__":
    unittest.main()
