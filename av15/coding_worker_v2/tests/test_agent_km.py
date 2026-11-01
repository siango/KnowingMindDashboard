"""V3 km entry tests: plan/status/tools/qc/resume/fingerprint."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import km_main  # noqa: E402


class TestKm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "ws").mkdir()
        self.base = self.root / "base"
        self.base.mkdir()
        self.args = ["--workspace", str(self.root / "ws"),
                     "--base", str(self.base)]

    def tearDown(self):
        self.tmp.cleanup()

    def run_km(self, argv):
        with mock.patch("sys.stdout") as stdout:
            rc = km_main.main(self.args + argv)
        out = "".join(str(call.args[0]) for call in stdout.write.call_args_list
                      if call.args)
        return rc, out

    def test_plan_simple(self):
        rc, _ = self.run_km(["plan", "--simple", "--plan-id", "px",
                             "fix the thing"])
        self.assertEqual(rc, 0)
        plan_file = self.base / "state" / "plans" / "px.json"
        self.assertTrue(plan_file.is_file())
        data = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "KM_TASK_PLAN_V1")

    def test_plan_revise(self):
        self.run_km(["plan", "--simple", "--plan-id", "pr", "first"])
        plan_file = str(self.base / "state" / "plans" / "pr.json")
        rc, _ = self.run_km(["plan", "--revise", plan_file,
                             "--feedback", "split it"])
        self.assertEqual(rc, 0)
        data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
        self.assertEqual(data["revision"], 2)

    def test_status_keys(self):
        rc, out = self.run_km(["status"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        for key in ("MISSION", "TASK", "OWNER_NODE", "PROGRESS_PERCENT",
                    "ACTIVE_MODEL", "ACTIVE_TOOL", "CURRENT_STEP", "QC_STATUS",
                    "RETRY_COUNT", "EVIDENCE", "NEXT_ACTION"):
            self.assertIn(key, data)

    def test_tools_discover(self):
        rc, out = self.run_km(["tools"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(any(r["tool"] == "repo_inspect" for r in data))

    def test_tools_call_validates(self):
        rc, out = self.run_km(["tools", "--call", "repo_inspect",
                               "--args", json.dumps(
                                   {"repo": str(self.root / "ws")})])
        self.assertEqual(rc, 0)
        self.assertIn('"ok": true', out)

    def test_tools_call_rejects_unknown(self):
        rc, out = self.run_km(["tools", "--call", "nope"])
        self.assertEqual(rc, 1)
        self.assertIn("TOOL_UNKNOWN", out)

    def test_fingerprint(self):
        rc, out = self.run_km(["fingerprint", "--repo", str(self.root / "ws"),
                               "--goal", "Fix It"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(len(data["fingerprint"]), 64)

    def test_resume(self):
        rc, out = self.run_km(["resume"])
        self.assertEqual(rc, 0)
        self.assertIn("recovered", out)


if __name__ == "__main__":
    unittest.main()
