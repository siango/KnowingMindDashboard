"""V3 km entry tests: plan/status/tools/qc/resume/fingerprint."""
import json
import os
import shutil
import subprocess
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

    def test_explicit_scope_survives_model_failure_and_revision(self):
        (self.root / "ws" / "calc.py").write_text("value = 1\n")
        self.run_km(["plan", "--simple", "--plan-id", "pr", "fix calc.py"])
        plan_file = self.base / "state" / "plans" / "pr.json"
        with mock.patch.object(km_main.agent_exec, "qwen_chain",
                               return_value=[]):
            rc, _ = self.run_km([
                "plan", "--revise", str(plan_file),
                "--allowed-path", "calc.py"])
        self.assertEqual(rc, 0)
        data = json.loads(plan_file.read_text())
        self.assertEqual(data["revision"], 2)
        self.assertEqual(data["engine"], "deterministic-fallback")
        self.assertEqual(data["tasks"][0]["allowed_paths"], ["calc.py"])

    def test_explicit_scope_replaces_model_selected_paths(self):
        (self.root / "ws" / "calc.py").write_text("value = 1\n")
        model_plan = km_main.agent_planner.simple_plan("fix", "M", "p").to_dict()
        model_plan["tasks"][0]["allowed_paths"] = ["unapproved.py"]
        provider = mock.Mock()
        provider.generate_plan.return_value = mock.Mock(
            text=json.dumps(model_plan), provider="qwen-local",
            model="qwen", latency_ms=1)
        with mock.patch.object(km_main.agent_exec, "qwen_chain",
                               return_value=[provider]):
            rc, _ = self.run_km(["plan", "--plan-id", "px", "fix calc.py",
                                 "--allowed-path", "calc.py"])
        self.assertEqual(rc, 0)
        data = json.loads((self.base / "state" / "plans" / "px.json").read_text())
        self.assertEqual(data["tasks"][0]["allowed_paths"], ["calc.py"])

    def test_invalid_or_readonly_scope_does_not_overwrite_plan(self):
        (self.root / "ws" / "calc.py").write_text("value = 1\n")
        (self.root / "ws" / "escape.py").symlink_to(
            self.root / "base" / "outside.py")
        (self.base / "outside.py").write_text("value = 0\n")
        self.run_km(["plan", "--simple", "--plan-id", "px", "original"])
        path = self.base / "state" / "plans" / "px.json"
        original = path.read_bytes()
        cases = [
            ("fix", ["missing.py"]), ("fix", ["../base/outside.py"]),
            ("fix", ["escape.py"]), ("fix", ["calc.py", "calc.py"]),
            ("Inspect repository without modifying files", ["calc.py"]),
        ]
        for command, paths in cases:
            with self.subTest(command=command, paths=paths):
                options = [arg for path in paths for arg in ("--allowed-path", path)]
                rc, _ = self.run_km(["plan", "--simple", "--revise", str(path),
                                     command, *options])
                self.assertEqual(rc, 1)
                self.assertEqual(path.read_bytes(), original)

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


class TestPinnedLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        launcher = Path(km_main.__file__).resolve().parents[2] / "km"
        shutil.copyfile(launcher, self.runtime / "km")
        self.source = self.root / "source"
        package = self.source / "coding_worker_v2" / "agent"
        package.mkdir(parents=True)
        (package / "km_main.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'args': sys.argv[1:],"
            "'pointer_dir': os.environ['KMW2_STATE_DIR']}))\n")
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(self.source), "-c", "user.name=test",
            "-c", "user.email=test@example.invalid", "commit", "-qm", "source"],
            check=True)
        self.sha = subprocess.check_output(
            ["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        self.env = {**os.environ, "KMW2_SOURCE_ROOT": str(self.source),
                    "KMW2_SOURCE_SHA": self.sha}
        self.env.pop("KMW2_STATE_DIR", None)

    def launch(self):
        return subprocess.run(["bash", str(self.runtime / "km"), "status"],
                              env=self.env, capture_output=True, text=True)

    def test_pinned_source_keeps_existing_store_and_pointer(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["args"], [
            "--base", str(self.runtime / "coding_worker_v2"),
            "--workspace", str(self.runtime), "status"])
        self.assertEqual(data["pointer_dir"], str(self.runtime / "state"))
        self.assertIn(f"source_sha={self.sha}", result.stderr)

    def test_missing_or_wrong_pin_fails_closed(self):
        for sha in ("", "0" * 40):
            with self.subTest(sha=sha):
                self.env["KMW2_SOURCE_SHA"] = sha
                result = self.launch()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_dirty_worker_source_fails_closed(self):
        (self.source / "coding_worker_v2" / "agent" / "km_main.py").write_text(
            "raise RuntimeError('must not execute')\n")
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn("KMCW_SOURCE_DIRTY", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
