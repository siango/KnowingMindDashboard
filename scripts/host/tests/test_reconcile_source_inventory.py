"""Offline tests for non-mutating source inventory and preservation gates."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "reconcile-source-inventory.py"
spec = importlib.util.spec_from_file_location("reconcile_source_inventory", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def git(path, *argv):
    return subprocess.run(["git", "-C", str(path), *argv], capture_output=True,
                          text=True, check=True).stdout.strip()

class InventoryTests(unittest.TestCase):
    def make_repo(self, base):
        path = base / "KnowingMindDashboard"
        path.mkdir()
        git(path, "init", "-q")
        git(path, "config", "user.email", "fixture@example.invalid")
        git(path, "config", "user.name", "Fixture")
        (path / "tracked.txt").write_text("baseline")
        git(path, "add", "tracked.txt")
        git(path, "commit", "-qm", "fixture")
        git(path, "remote", "add", "origin",
            "https://github.com/siango/KnowingMindDashboard.git")
        return path

    def test_clean_checkout_is_source_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = self.make_repo(base)
            result = mod.inventory([base], "ubuntu-usb")
            self.assertEqual(result["repository_count"], 1)
            record = result["repositories"][0]
            self.assertEqual(record["github_repository"], "siango/KnowingMindDashboard")
            self.assertEqual(record["tracked_changes"], 0)
            self.assertEqual(record["untracked_files"], 0)
            self.assertFalse(record["uncommitted_data_preservation_required"])
            self.assertEqual(result["production_cutover"], "NOT_GRANTED")
            self.assertTrue((path / "tracked.txt").is_file())

    def test_dirty_worktree_requires_preservation_and_never_fixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = self.make_repo(base)
            (path / "tracked.txt").write_text("changed")
            (path / "untracked.txt").write_text("preserve me")
            result = mod.inventory([base], "wsl")
            self.assertEqual(result["requires_preservation"], 1)
            self.assertEqual(result["repositories"][0]["tracked_changes"], 1)
            self.assertEqual(result["repositories"][0]["untracked_files"], 1)
            self.assertEqual((path / "tracked.txt").read_text(), "changed")
            self.assertEqual((path / "untracked.txt").read_text(), "preserve me")

    def test_absent_root_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = mod.inventory([Path(tmp) / "missing"], "ubuntu-ssd")
            self.assertEqual(result["repository_count"], 0)
            self.assertEqual(result["other_host_coverage"], "NOT_INSPECTED")
            self.assertEqual(result["postgresql_backup"], "NOT_VERIFIED")

if __name__ == "__main__":
    unittest.main()
