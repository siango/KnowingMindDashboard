"""Phase 2/3 tests: tasks, pipeline, queue, receipts."""
import json
import hashlib
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.config import WorkerConfig  # noqa: E402
from coding_worker_v2.pipeline import (PipelineError, bounded_edit,  # noqa: E402
                                       coding_messages, inspect_repo, list_files,
                                       make_patch, parse_edits, run_tests,
                                       syntax_check)
from coding_worker_v2.queue_store import QueueLeaseError, QueueStore  # noqa: E402
from coding_worker_v2.receipts import read_receipt, write_receipt  # noqa: E402
from coding_worker_v2.tasks import Task, TaskInput, TaskValidationError, admit  # noqa: E402


def make_task(**over):
    data = {"mission_id": "KM-TEST", "task_id": "t1", "owner_node": "AV15",
            "input": {"repo": "/tmp", "goal": "do thing"}}
    data.update(over)
    return Task.from_dict(data)


def _claim_in_process(base: str, results) -> None:
    task = QueueStore(Path(base)).claim()
    results.put(task.task_id if task else "")


class TestTasks(unittest.TestCase):
    def test_admit_ok(self):
        task = make_task()
        self.assertEqual(task.status, "ADMITTED")
        self.assertEqual(task.idempotency_key, "KM-TEST:t1")
        self.assertIn("next_action", task.to_dict())

    def test_reject_bad_id(self):
        with self.assertRaises(TaskValidationError):
            make_task(task_id="bad id!")

    def test_reject_empty_goal(self):
        with self.assertRaises(TaskValidationError):
            make_task(input={"repo": "/tmp", "goal": ""})

    def test_reject_repairs_range(self):
        with self.assertRaises(TaskValidationError):
            make_task(input={"repo": "/tmp", "goal": "g", "max_repairs": 99})

    def test_envelope_roundtrip(self):
        task = make_task()
        task.output = {"x": 1}
        clone = Task.from_dict(json.loads(json.dumps(task.to_dict())))
        self.assertEqual(clone.output, {"x": 1})


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "a.py").write_text("X = 1\n")
        self.config = WorkerConfig()

    def tearDown(self):
        self.tmp.cleanup()

    def test_inspect_missing(self):
        info = inspect_repo(str(self.repo / "nope"))
        self.assertFalse(info["exists"])

    def test_bounded_edit_replace(self):
        changed = bounded_edit(str(self.repo),
                               [{"path": "a.py", "old": "X = 1", "new": "X = 2"}],
                               self.config)
        self.assertEqual(changed, ["a.py"])
        self.assertIn("X = 2", (self.repo / "a.py").read_text())

    def test_bounded_edit_rejects_path_outside_allowlist(self):
        with self.assertRaises(PipelineError) as ctx:
            bounded_edit(str(self.repo),
                         [{"path": "new.py", "content": "x = 1\n"}],
                         self.config, allowed_paths=["a.py"])
        self.assertEqual(ctx.exception.failure_class, "POLICY_DENY")
        self.assertFalse((self.repo / "new.py").exists())

    def test_edit_escape_denied(self):
        with self.assertRaises(PipelineError) as ctx:
            bounded_edit(str(self.repo), [{"path": "../evil.py", "content": "x"}],
                         self.config)
        self.assertEqual(ctx.exception.failure_class, "POLICY_DENY")

    def test_edit_old_missing(self):
        with self.assertRaises(PipelineError) as ctx:
            bounded_edit(str(self.repo), [{"path": "a.py", "old": "zzz", "new": "y"}],
                         self.config)
        self.assertEqual(ctx.exception.failure_class, "PATCH_FAIL")

    def test_syntax_ok_and_bad(self):
        ok = syntax_check(str(self.repo), ["a.py"])
        self.assertTrue(ok["ok"])
        (self.repo / "b.py").write_text("def broken(:\n")
        bad = syntax_check(str(self.repo), ["b.py"])
        self.assertFalse(bad["ok"])

    def test_parse_edits_strict(self):
        edits = parse_edits('{"edits": [{"path": "a", "content": "b"}]}')
        self.assertEqual(len(edits), 1)
        with self.assertRaises(PipelineError):
            parse_edits("not json at all {{{")

    def test_run_tests_allowlist(self):
        with self.assertRaises(PipelineError) as ctx:
            run_tests(str(self.repo), "rm -rf /", 10)
        self.assertEqual(ctx.exception.failure_class, "POLICY_DENY")
        (self.repo / "test_s.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n def test_x(self): pass\n")
        result = run_tests(str(self.repo), "python3 -m unittest -v", 60)
        self.assertTrue(result["ran"])
        self.assertEqual(result["rc"], 0)


class TestQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QueueStore(Path(self.tmp.name) / "q")

    def tearDown(self):
        self.tmp.cleanup()

    def test_enqueue_idempotent(self):
        task_id, created = self.store.enqueue(make_task())
        self.assertTrue(created)
        task_id2, created2 = self.store.enqueue(make_task())
        self.assertFalse(created2)
        self.assertEqual(task_id, task_id2)
        self.assertEqual(self.store.health()["inbox"], 1)

    def test_stale_lease_token_is_fenced_after_recovery(self):
        self.store.enqueue(make_task())
        stale = self.store.claim()
        assert stale is not None
        lease = self.store.doing / f"{stale.task_id}.lease.json"
        data = json.loads(lease.read_text())
        data["expires_at"] = time.time() - 1
        lease.write_text(json.dumps(data))
        self.store.recover()
        current = self.store.claim()
        assert current is not None
        self.assertNotEqual(stale.lease_token, current.lease_token)
        self.assertFalse(self.store.heartbeat(stale.task_id, stale.lease_token))
        with self.assertRaises(QueueLeaseError):
            self.store.complete(stale)
        self.store.complete(current)

    def test_claim_heartbeat_complete(self):
        self.store.enqueue(make_task())
        task = self.store.claim()
        self.assertIsNotNone(task)
        assert task is not None
        self.assertTrue(self.store.lease_valid(task.task_id))
        self.assertTrue(self.store.heartbeat(task.task_id, task.lease_token))
        self.store.complete(task)
        health = self.store.health()
        self.assertEqual(health["done"], 1)
        self.assertEqual(health["doing"], 0)

    def test_no_duplicate_active(self):
        self.store.enqueue(make_task())
        first = self.store.claim()
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.claim())

    def test_concurrent_process_claim_has_one_owner(self):
        self.store.enqueue(make_task())
        results = multiprocessing.Queue()
        workers = [multiprocessing.Process(
            target=_claim_in_process, args=(str(self.store.base), results))
                   for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
        claims = [results.get(timeout=2) for _ in workers]
        self.assertEqual(claims.count("t1"), 1)
        self.assertEqual(claims.count(""), len(workers) - 1)

    def test_expired_lease_recovers(self):
        self.store.enqueue(make_task())
        task = self.store.claim()
        assert task is not None
        lease = self.store.doing / f"{task.task_id}.lease.json"
        data = json.loads(lease.read_text())
        data["expires_at"] = time.time() - 1
        lease.write_text(json.dumps(data))
        self.assertFalse(self.store.heartbeat(task.task_id, task.lease_token))
        recovered = self.store.recover()
        self.assertEqual(recovered["recovered"], [task.task_id])
        self.assertEqual(self.store.health()["inbox"], 1)

    def test_fail_requeue_then_exile(self):
        task = make_task()
        task.max_attempts = 2
        self.store.enqueue(task)
        claimed = self.store.claim()
        assert claimed is not None
        self.assertEqual(self.store.fail(claimed, "TEST_FAIL", "RETRY"), "requeued")
        claimed2 = self.store.claim()
        assert claimed2 is not None
        self.assertEqual(self.store.fail(claimed2, "TEST_FAIL", "RETRY"), "failed")
        self.assertEqual(self.store.health()["failed"], 1)

    def test_checkpoint_roundtrip(self):
        self.store.save_checkpoint("t9", "edited", {"changed": ["a.py"]})
        loaded = self.store.load_checkpoint("t9")
        assert loaded is not None
        self.assertEqual(loaded["stage"], "edited")


class TestReceipts(unittest.TestCase):
    def test_write_read_roundtrip_and_scrub(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = make_task()
            task.status = "DONE"
            task.output = {"changed": ["a.py"]}
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp, "KMW2_DEEPSEEK_API_KEY": "sk-live-secret-9"}):
                task.evidence.append({"note": "used sk-live-secret-9"})
                path = write_receipt(Path(tmp) / "receipts", task)
            raw = path.read_text()
            self.assertNotIn("sk-live-secret-9", raw)
            back = read_receipt(path)
            from pathlib import Path as _P
            self.assertTrue((_P(tmp) / "coding_worker_v2_last.json").exists())
            self.assertEqual(back["task_id"], "t1")
            first_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            task.status = "FAILED"
            with mock.patch.dict(os.environ, {"KMW2_STATE_DIR": tmp}):
                second_path = write_receipt(Path(tmp) / "receipts", task)
            second = read_receipt(second_path)
            self.assertEqual(second["receipt_version"], 2)
            self.assertEqual(second["previous_receipt_sha256"], first_digest)
            self.assertEqual(read_receipt(path)["status"], "DONE")
            self.assertNotEqual(path, second_path)
            for key in ("mission_id", "task_id", "owner_node", "input", "output",
                        "status", "evidence", "next_action"):
                self.assertIn(key, back)

    def test_read_rejects_non_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text('{"nope": true}')
            with self.assertRaises(ValueError):
                read_receipt(path)


class TestFileListAndPatch(unittest.TestCase):
    def test_list_files_skips_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x")
            (root / ".hidden").write_text("x")
            (root / ".git").mkdir()
            (root / ".git" / "c").write_text("x")
            files = list_files(tmp)
            self.assertIn("a.py", files)
            self.assertNotIn(".hidden", files)
            self.assertFalse(any(f.startswith(".git") for f in files))

    def test_coding_messages_include_files(self):
        msgs = coding_messages("fix", {"files": 2}, "", files=["calc.py"])
        user = json.loads(msgs[1]["content"])
        self.assertEqual(user["files"], ["calc.py"])
        self.assertIn("approved file list", msgs[0]["content"])

    def test_coding_messages_no_files_ok(self):
        msgs = coding_messages("fix", {"files": 2})
        user = json.loads(msgs[1]["content"])
        self.assertNotIn("files", user)

    def test_make_patch_includes_untracked(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=root,
                           check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=root,
                           check=True)
            (root / "new.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "new.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root,
                           check=True)
            (root / "added.py").write_text("y = 2\n")
            config = WorkerConfig.from_env()
            diff = make_patch(tmp, ["added.py"], config)
            self.assertIn("added.py", diff)
            self.assertIn("y = 2", diff)


if __name__ == "__main__":
    unittest.main()
