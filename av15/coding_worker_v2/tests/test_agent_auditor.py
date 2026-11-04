"""Tests for fail-closed access to the pinned Mission Auditor."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import auditor  # noqa: E402


class TestMissionAuditor(unittest.TestCase):
    def test_pinned_authority_identity(self):
        self.assertEqual(auditor.REQUIREMENT_COUNT, 31)
        self.assertEqual(auditor.SCOPE_VERSION, 2)
        self.assertEqual(len(auditor.AUDITOR_COMMIT), 40)
        self.assertTrue(auditor.SCOPE_DIGEST.startswith("sha256:"))

    def test_missing_approved_source_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(auditor.MissionAuditorError) as ctx:
                auditor.run_mission_audit("ADMIT")
        self.assertEqual(ctx.exception.reason, "AUDITOR_UNAVAILABLE")

    def test_wrong_stage_rejected(self):
        with self.assertRaises(auditor.MissionAuditorError) as ctx:
            auditor.run_mission_audit("DEPLOY")
        self.assertEqual(ctx.exception.reason, "AUDIT_STAGE_INVALID")

    def test_unapproved_or_incomplete_audit_cannot_pass_gate(self):
        report = {"schema": "KM_MISSION_AUDIT_REPORT_V1",
                  "requirements_total": 31, "scope_version": 2,
                  "scope_digest": auditor.SCOPE_DIGEST,
                  "requirements_missing": 31, "global_findings": [],
                  "matrix": []}
        self.assertFalse(auditor._gate_passed(report))

    def test_complete_source_only_report_is_not_production_acceptance(self):
        report = {"schema": "KM_MISSION_AUDIT_REPORT_V1",
                  "requirements_total": 31, "scope_version": 2,
                  "scope_digest": auditor.SCOPE_DIGEST,
                  "observed_requirements": 31,
                  "requirements_missing": 0, "global_findings": [],
                  "matrix": [{"requirement_id": f"KM{i:02d}",
                              "status": "EVIDENCE_CANDIDATE_ONLY"}
                             for i in range(1, 32)],
                  "verdict": "NO_PRODUCTION_ACCEPTANCE"}
        self.assertTrue(auditor._gate_passed(report))
        self.assertEqual(report["verdict"], "NO_PRODUCTION_ACCEPTANCE")


if __name__ == "__main__":
    unittest.main()
