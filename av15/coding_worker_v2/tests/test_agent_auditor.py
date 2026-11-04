"""Tests for fail-closed access to the pinned Mission Auditor."""
import os
import json
import subprocess
import sys
import unittest
import hashlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import auditor  # noqa: E402


class TestMissionAuditor(unittest.TestCase):
    def valid_context(self):
        root = Path(__file__).resolve().parents[3]
        source_sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        tasks = [{"task_id": "t1", "goal": "change",
                  "allowed_paths": [
                      "av15/PHASE0_REUSE_GAP_V3_V3_1.md"],
                  "acceptance": ["ok"]}]
        snapshot = {
            "schema": "KM_TASK_PLAN_V1", "mission_id": "M", "plan_id": "P",
            "owner_node": "AV15", "source_command": "change",
            "revision": 1, "tasks": tasks,
        }
        return {
            "mission_id": "M", "plan_id": "P", "revision": 1,
            "owner_node": "AV15", "source_command": "change",
            "workspace": str(root),
            "plan_digest": hashlib.sha256(json.dumps(
                snapshot, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")).hexdigest(),
            "scope_digest": auditor.SCOPE_DIGEST,
            "source_sha": source_sha,
            "plan_snapshot": snapshot, "tasks": tasks,
        }

    def test_pinned_authority_identity(self):
        self.assertEqual(auditor.REQUIREMENT_COUNT, 31)
        self.assertEqual(auditor.SCOPE_VERSION, 2)
        self.assertEqual(len(auditor.AUDITOR_COMMIT), 40)
        self.assertTrue(auditor.SCOPE_DIGEST.startswith("sha256:"))

    def test_missing_approved_source_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(auditor.MissionAuditorError) as ctx:
                auditor.run_mission_audit("ADMIT", {})
        self.assertEqual(ctx.exception.reason, "AUDIT_CONTEXT_INVALID")

    def test_stage_context_is_required_and_source_sha_must_match(self):
        with self.assertRaises(auditor.MissionAuditorError) as ctx:
            auditor.run_mission_audit("BEFORE_MUTATION", {
                "mission_id": "M", "plan_id": "P", "revision": 1,
                "owner_node": "AV15", "source_command": "change",
                "workspace": "/missing", "plan_digest": "0" * 64,
                "source_sha": "0" * 40,
            })
        self.assertEqual(ctx.exception.reason, "AUDIT_CONTEXT_INVALID")

    def test_missing_approved_source_fails_closed_after_valid_context(self):
        context = self.valid_context()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(auditor.MissionAuditorError) as ctx:
                auditor.run_mission_audit("ADMIT", context)
        self.assertEqual(ctx.exception.reason, "AUDITOR_UNAVAILABLE")

    def test_stale_source_context_is_rejected(self):
        context = self.valid_context()
        context["source_sha"] = "0" * 40
        with self.assertRaises(auditor.MissionAuditorError) as ctx:
            auditor.run_mission_audit("ADMIT", context)
        self.assertEqual(ctx.exception.reason, "AUDIT_CONTEXT_INVALID")

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
