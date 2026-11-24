"""Offline regression tests for SSD/USB host readiness: no real host probes."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "ssd-readiness.py"
spec = importlib.util.spec_from_file_location("ssd_readiness", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "a" * 40
OTHER = "b" * 40

class ReadinessTests(unittest.TestCase):
    def test_unconfigured_host_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(mod, "probe", return_value=(None, "")):
                result = mod.inspect(Path(temp), "ubuntu-ssd", SHA)
            self.assertEqual(result["status"], "HOST_PRECHECK_BLOCKED")
            self.assertEqual(result["production_authority"], "NOT_GRANTED")
            self.assertEqual(result["database_readback"], "NOT_PERFORMED")
            self.assertEqual(result["mutations"], 0)
            self.assertFalse(any(v["state"] == "PASS" for k,v in result["checks"].items() if k != "disk_space"))

    def test_exact_source_bound_host_precheck_not_production(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home/"kmcp-host-setup/.git").mkdir(parents=True)
            managed = home/"KnowingMind/.venvs/kmcp-portable/.knowingmind-managed.json"
            managed.parent.mkdir(parents=True)
            managed.write_text(json.dumps({"state": "INSTALLED", "source_sha": SHA}))
            cfg = home/".config/knowingmind/kmcw-owner-runtime.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("{}")
            cfg.chmod(0o600)
            runner = home/"KnowingMind/runners/github/.runner"
            runner.parent.mkdir(parents=True)
            runner.write_text("{}")
            def probe(argv, timeout=4):
                if argv[0] == "git": return 0, SHA
                return 0, ""
            with patch.object(mod, "probe", side_effect=probe), patch.object(
                    mod.shutil, "disk_usage", return_value=SimpleNamespace(free=2 * 1024**3)):
                result = mod.inspect(home, "ubuntu-ssd", SHA)
            self.assertEqual(result["status"], "HOST_PRECHECK_PASS_NOT_PRODUCTION_ACCEPTANCE")
            self.assertEqual(result["independent_qc"], "NOT_PERFORMED")
            self.assertEqual(result["production_authority"], "NOT_GRANTED")
            self.assertNotIn("token", json.dumps(result).lower())

    def test_source_drift_and_unsafe_config_are_blockers(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home/"kmcp-host-setup/.git").mkdir(parents=True)
            marker = home/"KnowingMind/.venvs/kmcp-portable/.knowingmind-managed.json"
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"state": "INSTALLED", "source_sha": OTHER}))
            cfg = home/".config/knowingmind/kmcw-owner-runtime.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text('{"secret":"do-not-read"}')
            cfg.chmod(0o644)
            with patch.object(mod, "probe", return_value=(0, SHA)):
                result = mod.inspect(home, "ubuntu-usb", SHA)
            self.assertEqual(result["checks"]["source_alignment"]["state"], "BLOCKED")
            self.assertEqual(result["checks"]["owner_config"]["state"], "BLOCKED")
            self.assertNotIn("do-not-read", json.dumps(result))

    def test_invalid_node_and_sha_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                mod.inspect(Path(temp), "ubuntu-wrong", SHA)
            with self.assertRaises(ValueError):
                mod.inspect(Path(temp), "ubuntu-ssd", "not-a-sha")

if __name__ == "__main__":
    unittest.main()
