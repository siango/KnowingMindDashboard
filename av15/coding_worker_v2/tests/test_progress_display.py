"""Progress rendering plus fail-closed source integration checks."""
import io
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from coding_worker_v2.agent.progress import LiveProgress
from coding_worker_v2.agent import planner, executor
from coding_worker_v2.config import WorkerConfig
from coding_worker_v2.queue_store import QueueStore
class TTY(io.StringIO):
    def isatty(self):
        return True
class TestProgressDisplay(unittest.TestCase):
    def test_wait_ticks_without_fake_progress(self):
        stream = io.StringIO()
        with LiveProgress('plan', stream) as display:
            display.update(phase='QWEN_PLAN', state='WAITING', total=4,
                           done=1, model='qwen', timeout=600)
            time.sleep(1.1)
        lines = stream.getvalue().splitlines()
        waiting = [line for line in lines if 'Waiting for Qwen' in line]
        self.assertGreaterEqual(len(waiting), 2)
        self.assertIn('elapsed=1.', waiting[-1])
        self.assertTrue(all('progress=25%' in line for line in waiting))
        self.assertTrue(all('timeout=600s' in line for line in waiting))
        self.assertNotIn('\033', stream.getvalue())

    def test_tty_and_error_finish(self):
        stream = TTY()
        with self.assertRaises(ValueError):
            with LiveProgress('run', stream):
                raise ValueError('bad plan')
        self.assertIn('\r\033[2K', stream.getvalue())
        self.assertIn('BLOCKED phase=ERROR', stream.getvalue())
        self.assertTrue(stream.getvalue().endswith('\n'))

    def test_readonly_plan_keeps_intent_and_source_executor_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plan=planner.simple_plan('Inspect repository without modifying files','KMCW-V3','readonly')
            self.assertEqual(plan.tasks[0].allowed_paths,[])
            self.assertNotIn('implemented',' '.join(plan.tasks[0].acceptance))
            with patch('coding_worker_v2.agent.executor.run_mission_audit', side_effect=AssertionError('mutation audit reached')):
                report=executor.execute_plan(plan,WorkerConfig(),QueueStore(root/'queue'),root/'receipts',root/'state',str(root),use_worktree=False)
            self.assertEqual(report['status'],'BLOCKED')
            self.assertEqual(report['done'],0)
            self.assertEqual(report['next_action'],'READ_ONLY_TOOL_RUNTIME_NOT_RECONCILED')

    def test_planner_rejects_truncated_json_and_accepts_completed_thinking(self):
        self.assertEqual(planner.parse_plan('<think>reason</think>```json\n{"tasks":[]}\n```'),{'tasks':[]})
        with self.assertRaises(planner.PlanError):
            planner.parse_plan('{"tasks":[')

    def test_qwen_schema_transport(self):
        from coding_worker_v2.providers import QwenProvider
        provider=QwenProvider(WorkerConfig())
        with patch.object(provider,'_candidates',return_value=[('qwen','')]),patch.object(provider,'_chat') as chat:
            provider.generate_plan([],response_schema=planner.PLAN_OUTPUT_SCHEMA)
            self.assertEqual(chat.call_args.kwargs['structured'],planner.PLAN_OUTPUT_SCHEMA)
