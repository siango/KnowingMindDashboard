"""V3 memory tests: fingerprints and dedup classification."""
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from coding_worker_v2.agent import memory  # noqa: E402
from coding_worker_v2.queue_store import QueueStore  # noqa: E402
from coding_worker_v2.receipts import write_receipt  # noqa: E402
from coding_worker_v2.tasks import Task  # noqa: E402


def make_task(goal, repo, base="abc123", test_command=""):
    return Task.from_dict({
        "mission_id": "M1", "task_id": "t1", "owner_node": "AV15",
        "input": {"repo": repo, "goal": goal, "test_command": test_command,
                  "hints": {"base_commit": base}}})


class TestFingerprint(unittest.TestCase):
    def test_deterministic(self):
        a = memory.fingerprint_for("Fix It", "/r", "c1")
        b = memory.fingerprint_for("  fix   it ", "/r", "c1")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_repo_and_base_matter(self):
        a = memory.fingerprint_for("same", "/r1", "c1")
        b = memory.fingerprint_for("same", "/r2", "c1")
        c = memory.fingerprint_for("same", "/r1", "c2")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "repo").mkdir()
        self.store = QueueStore(self.root / "q")
        self.receipts = self.root / "receipts"
        self._patch = mock.patch.dict(os.environ, {"KMW2_STATE_DIR": self.tmp.name})
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_execution_context_compares_six_facets_without_credentials(self):
        import copy
        from types import SimpleNamespace
        source=self.root/'repo'/'source.py';source.write_text('# first')
        ctx={'workspace':str(self.root/'repo'),'store':self.store,'receipts_dir':self.receipts,
            'pilot':{'test_command':'python3 -m unittest'},
            'config':SimpleNamespace(ollama_url='http://127.0.0.1:11434'),
            'km_admin_binding':{'session_cookie':'secret-sentinel-never-exported'}}
        first=memory.execution_context(ctx,'a'*40,['source.py'])
        self.assertEqual(set(first['facets']),{'path','variables','version','environment','authority','resource'})
        self.assertEqual(memory.compare_context(first,memory.execution_context(ctx,'a'*40,['source.py']))['state'],'MATCH')
        self.assertNotIn('secret-sentinel',json.dumps(first))
        variants=[]
        source.write_text('# second');variants.append(('path',memory.execution_context(ctx,'a'*40,['source.py'])));source.write_text('# first')
        other={**ctx,'pilot':{'test_command':'python3 -m unittest changed'}}
        variants.append(('variables',memory.execution_context(other,'a'*40,['source.py'])))
        variants.append(('version',memory.execution_context(ctx,'b'*40,['source.py'])))
        with mock.patch('platform.machine',return_value='isolated-new-machine'):
            variants.append(('environment',memory.execution_context(ctx,'a'*40,['source.py'])))
        other={**ctx,'command_auth':{'authenticated':False,'principal_id':'fixture-denied'}}
        variants.append(('authority',memory.execution_context(other,'a'*40,['source.py'])))
        other={**ctx,'receipts_dir':self.root/'different-receipts'}
        variants.append(('resource',memory.execution_context(other,'a'*40,['source.py'])))
        for facet,value in variants:
            self.assertIn(facet,memory.compare_context(first,value)['different_facets'])
        self.assertEqual(memory.compare_context(None,first)['state'],'UNKNOWN')

    def test_scoped_observed_learning_retrieval_keeps_original_qc_boundary(self):
        import copy,datetime
        from coding_worker_v2.agent.executor import learning_record
        from coding_worker_v2.agent.self_development import digest
        root=self.root/'repo';source=root/'source.py';source.write_text('# isolated source')
        original={'event':'learning.recorded','record_id':'isolated-observation',
            'classification':'OBSERVED','source_sha':'a'*40,
            'learning':learning_record(problem='registry binding loss',cause=[],criterion='retain client')}
        original['record_sha256']=digest(original)
        row={'event':'memory.indexed','record_id':'isolated-index','classification':'OBSERVED',
            'error_signature':'REGISTRY_REBUILT_BINDING_LOSS','scope':{
                'repo':str(root),'account_id':None,'owner_uid':os.geteuid()},'source_sha':'a'*40,
            'source_files_sha256':{'source.py':hashlib.sha256(source.read_bytes()).hexdigest()},
            'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'learning_record_id':original['record_id'],
            'learning_record_sha256':original['record_sha256'],
            'memory_case':{'PROBLEM':{'failure_class':'REGISTRY_REBUILT_BINDING_LOSS'},
                'CAUSE':{'evidenced':[]},'RESULT':{'state':'ISOLATED_PASS'},
                'SOLUTION_PATH':{'rule':'reuse supplied registry'}}}
        def save(value,first=None):
            value['record_sha256']=digest({k:v for k,v in value.items() if k!='record_sha256'})
            self.store.save_checkpoint('.tool-audit','tool_policy_audit',{'events':[first or original,value]})
        def read(query='REGISTRY_REBUILT_BINDING_LOSS',account=None,sha='a'*40):
            return memory.planning_lessons(query,str(root),sha,self.store,self.receipts,account_id=account)
        save(row)
        found=read()['lessons'];self.assertEqual(len(found),1)
        self.assertEqual(found[0]['classification'],'OBSERVED')
        self.assertIsNone(found[0]['recommendation'])
        self.assertEqual(read('prefixREGISTRY_REBUILT_BINDING_LOSS')['lessons'],[])
        self.assertEqual(read(account='different-account')['lessons'],[])
        self.assertEqual(read(sha='b'*40)['lessons'],[])
        for field,value in [('repo','/another-workspace'),('owner_uid',os.geteuid()+1)]:
            changed=copy.deepcopy(row);changed['scope'][field]=value;save(changed)
            self.assertEqual(read()['lessons'],[])
        save(row);source.write_text('# changed source')
        self.assertEqual(read()['lessons'],[]);source.write_text('# isolated source')
        changed=copy.deepcopy(row);changed['observed_at']='2000-01-01T00:00:00+00:00';save(changed)
        stale=read();self.assertEqual(stale['lessons'],[])
        self.assertEqual(stale['excluded'][0]['classification'],'STALE')
        changed=copy.deepcopy(original);changed['learning']['magga_state']='PROVEN_BY_VERIFIED_RECEIPT'
        changed['record_sha256']=digest({k:v for k,v in changed.items() if k!='record_sha256'})
        altered=copy.deepcopy(row);altered['learning_record_sha256']=changed['record_sha256'];save(altered,changed)
        self.assertEqual(read()['lessons'],[])
        save(row);saved=self.store.load_checkpoint('.tool-audit');saved['data']['events'][0]['source_sha']='tampered'
        self.store.save_checkpoint('.tool-audit','tool_policy_audit',saved['data'])
        self.assertEqual(read()['lessons'],[])

    def _archive_terminal(self, task, folder):
        task.lease_token = "memory-test-lease"
        record = task.to_dict()
        record["lease_token_sha256"] = hashlib.sha256(
            task.lease_token.encode()).hexdigest()
        self.store._write_json(folder / task.task_id / "task.json", record)

    def test_new(self):
        out = memory.classify("do work", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "NEW")

    def test_blocked_repo_missing(self):
        out = memory.classify("do work", str(self.root / "nothere"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "BLOCKED")

    def test_monitor_active_lease(self):
        task = make_task("queued work", str(self.root / "repo"))
        self.store.enqueue(task)
        claimed = self.store.claim()
        self.assertIsNotNone(claimed)
        out = memory.classify("queued work", str(self.root / "repo"), "abc123",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "MONITOR")

    def test_resume_expired_lease_checkpoint(self):
        task = make_task("stuck work", str(self.root / "repo"))
        self.store.enqueue(task)
        claimed = self.store.claim()
        assert claimed is not None
        lease = self.store.doing / f"{claimed.task_id}.lease.json"
        data = json.loads(lease.read_text(encoding="utf-8"))
        data["expires_at"] = time.time() - 1
        lease.write_text(json.dumps(data), encoding="utf-8")
        self.store.save_checkpoint(claimed.task_id, "edited", {"changed": ["a.py"]})
        out = memory.classify("stuck work", str(self.root / "repo"), "abc123",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "RESUME")

    def _done_receipt(self, goal, base, qc_pass=True, later_evidence=()):
        task = make_task(goal, str(self.root / "repo"), base)
        task.status = "DONE"
        task.output = {"changed": ["a.py"]}
        if qc_pass:
            task.evidence.append({"event": "qc_checked",
                                  "qc": {"passed": True, "independent_verified": True}})
            task.evidence.append({"event": "independent_verification", "proof": {
                "authenticated": True, "status": "PASS", "task_id": task.task_id,
                "source_sha": base, "signature_sha256": "a" * 64}})
        task.evidence.extend(later_evidence)
        self._archive_terminal(task, self.store.done)
        return write_receipt(self.receipts, task)

    def test_latest_failed_qc_demotes_previously_verified_work_memory(self):
        self._done_receipt('changed QC outcome', '1' * 40, later_evidence=[
            {'event':'qc_checked','qc':{'passed':False,'independent_verified':True}}])
        records=memory.retrieve_work('changed QC outcome',str(self.root/'repo'),
            '1'*40,self.store,self.receipts,ranking='qc')
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['RESULT']['state'],'EXECUTION_DONE')
        self.assertFalse(records[0]['verified_for_current_source'])
        self.assertEqual(records[0]['qc_score'],0)

    def test_latest_failed_independent_proof_demotes_work_memory(self):
        self._done_receipt('changed verifier outcome', '1' * 40, later_evidence=[
            {'event':'independent_verification','proof':{
                'authenticated':True,'status':'FAIL','task_id':'t1',
                'source_sha':'1'*40,'signature_sha256':'a'*64}}])
        records=memory.retrieve_work('changed verifier outcome',str(self.root/'repo'),
            '1'*40,self.store,self.receipts,ranking='qc')
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['RESULT']['state'],'EXECUTION_DONE')
        self.assertFalse(records[0]['verified_for_current_source'])
        self.assertEqual(records[0]['qc_score'],0)

    def test_receipt_verification_flags_are_strict_booleans_for_json_context(self):
        path=self._done_receipt('strict verification flag','1'*40)
        receipt=json.loads(path.read_text())
        self.assertIs(memory.receipt_verified(receipt),True)
        records=memory.retrieve_work('strict verification flag',str(self.root/'repo'),
            '1'*40,self.store,self.receipts,ranking='qc')
        self.assertIs(records[0]['verified_for_current_source'],True)
        self.assertEqual(json.loads(json.dumps(records))[0]['RESULT']['state'],'VERIFIED_PASS')
        receipt['evidence'].append({'event':'qc_checked','qc':{'passed':False}})
        self.assertIs(memory.receipt_verified(receipt),False)

    def test_reuse_exact_match(self):
        self._done_receipt("reuse me", "1" * 40)
        out = memory.classify("reuse me", str(self.root / "repo"), "1" * 40,
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REUSE")

    def test_four_part_memory_retains_original_provenance_without_false_verification(self):
        path = self._done_receipt("fix repeated problem", "1" * 40, qc_pass=False)
        original = path.read_bytes()
        records = memory.retrieve_work(
            "fix repeated problem", str(self.root / "repo"), "1" * 40,
            self.store, self.receipts)
        self.assertEqual(len(records), 1)
        case = records[0]
        self.assertEqual(case["RESULT"]["state"], "EXECUTION_DONE")
        self.assertFalse(case["verified_for_current_source"])
        self.assertEqual(case["provenance"]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(case["CAUSE"]["hypotheses"], [])
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(all(k in case for k in ("PROBLEM", "CAUSE", "SOLUTION_PATH", "RESULT")))

    def test_verified_solution_on_different_source_is_retrieval_only(self):
        self._done_receipt("verified fix", "1" * 40)
        records = memory.retrieve_work(
            "verified fix", str(self.root / "repo"), "2" * 40, self.store, self.receipts)
        self.assertEqual(records[0]["RESULT"]["state"], "VERIFIED_PASS")
        self.assertFalse(records[0]["verified_for_current_source"])
        self.assertTrue(records[0]["retrieval_only"])

    def test_conflicting_receipt_is_not_retrieved_as_a_work_lesson(self):
        base = "1" * 40
        self._done_receipt("verified fix", base)
        archive = next(self.store.done.glob("*/task.json"))
        data = json.loads(archive.read_text())
        data["status"] = "FAILED"
        archive.write_text(json.dumps(data))
        records = memory.retrieve_work(
            "verified fix", str(self.root / "repo"), base,
            self.store, self.receipts)
        self.assertEqual(records, [])

    def test_duplicate_task_source_mismatch_is_blocked(self):
        task = make_task("same task", str(self.root / "repo"), "old")
        self.store.enqueue(task)
        out = memory.classify(
            "same task", str(self.root / "repo"), "new",
            self.store, self.receipts, task_id=task.task_id)
        self.assertEqual(out.decision, "BLOCKED")
        self.assertEqual(out.reason, "SOURCE_OR_TASK_SCOPE_MISMATCH")

    def test_qc_ranking_prefers_verified_lesson_without_replacing_provenance(self):
        self._done_receipt("fix arithmetic addition", "1" * 40)
        second = make_task("fix arithmetic subtraction", str(self.root / "repo"), "1" * 40)
        second.task_id = "unverified"
        second.status = "FAILED"
        second.output = {"changed": ["calc.py"]}
        write_receipt(self.receipts, second)
        records = memory.retrieve_work(
            "arithmetic", str(self.root / "repo"), "1" * 40,
            self.store, self.receipts, ranking="qc")
        self.assertEqual(records[0]["task_id"], "t1")
        self.assertEqual(records[0]["qc_score"], 1)
        self.assertIn("py", records[0]["tags"])
        original = Path(records[0]["provenance"]["original_ref"])
        self.assertEqual(json.loads(original.read_text())["task_id"], "t1")

    def test_reverify_base_changed(self):
        self._done_receipt("shifting", "c1")
        out = memory.classify("shifting", str(self.root / "repo"), "c2",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REVERIFY")
        self.assertIn("BASE_COMMIT_CHANGED", out.reason)

    def test_reverify_stale(self):
        path = self._done_receipt("old news", "c1")
        old = time.time() - 999999
        os.utime(path, (old, old))
        out = memory.classify("old news", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REVERIFY")

    def test_reverify_no_qc(self):
        self._done_receipt("no qc proof", "c1", qc_pass=False)
        out = memory.classify("no qc proof", str(self.root / "repo"), "c1",
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REVERIFY")

    def test_repair_prior_failure(self):
        task = make_task("flaky", str(self.root / "repo"), "c1")
        task.status = "FAILED"
        task.failure_class = "TEST_FAIL"
        task.attempts = 1
        self._archive_terminal(task, self.store.failed)
        write_receipt(self.receipts, task)
        out = memory.classify("flaky", str(self.root / "repo"), "c1",
                              self.store, self.receipts, max_attempts=3)
        self.assertEqual(out.decision, "REPAIR")

    def test_reuse_matches_base_workspace_not_worktree(self):
        task = make_task("wt work", "/state/worktrees/x-t1", "1" * 40)
        task.task_input.hints["workspace"] = str(self.root / "repo")
        task.status = "DONE"
        task.output = {"changed": ["a.py"]}
        task.evidence.append({"event": "qc_checked", "qc": {
            "passed": True, "independent_verified": True}})
        task.evidence.append({"event": "independent_verification", "proof": {
            "authenticated": True, "status": "PASS", "task_id": task.task_id,
            "source_sha": "1" * 40, "signature_sha256": "a" * 64}})
        self._archive_terminal(task, self.store.done)
        write_receipt(self.receipts, task)
        out = memory.classify("wt work", str(self.root / "repo"), "1" * 40,
                              self.store, self.receipts)
        self.assertEqual(out.decision, "REUSE")

    def test_blocked_retries_exhausted(self):
        task = make_task("hopeless", str(self.root / "repo"), "c1")
        task.status = "FAILED"
        task.failure_class = "TEST_FAIL"
        task.attempts = 5
        self._archive_terminal(task, self.store.failed)
        write_receipt(self.receipts, task)
        out = memory.classify("hopeless", str(self.root / "repo"), "c1",
                              self.store, self.receipts, max_attempts=3)
        self.assertEqual(out.decision, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
