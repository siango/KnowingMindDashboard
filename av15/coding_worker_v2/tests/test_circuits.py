"""GitHub-runnable contracts: real file tools, typed DAGs, recovery and ACL.

All identities/planners in this file are isolated test fixtures, never runtime
credentials or independent production acceptance.
"""
import copy
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_worker_v2.agent import circuits as c, mcp_server
from coding_worker_v2.agent.tools import ToolError, ToolRegistry, ToolSpec, build_default_registry
from coding_worker_v2.agent.kmco_runtime import KMCOCatalog
from coding_worker_v2.queue_store import QueueStore
from coding_worker_v2.agent.circuit_cli import load_runtime, main


def obj(**fields):
    return {'type': 'object', 'required': list(fields), 'properties': fields,
            'additionalProperties': False}


def node(name, tool, arguments, schema, after=(), **extra):
    return {'id': name, 'tool': tool, 'arguments': arguments, 'after': list(after),
            'result_schema': schema, **extra}


def graph():
    return {'nodes': [node('one', 'fixture.source', {}, obj(text={'type': 'string'})),
        node('two', 'fixture.echo', {'text': {'$ref': 'one#/text'}},
             obj(text={'type': 'string'}), ['one'])],
        'outputs': {'answer': {'$ref': 'two#/text'}},
        'acceptance': [{'path': '/answer', 'schema': {'type': 'string', 'enum': ['evidence']}}]}


def catalog():
    records = []
    for prefix, count, filename in [('P', 5, 'core.md'), ('D', 6, 'domain.md')]:
        for i in range(1, 13):
            for j in range(1, count + 1):
                op = f'{prefix}{i:02d}-M{j:02d}'
                records.append({'operation_id': op, 'group_id': f'{prefix}{i:02d}',
                    'source_file': filename, 'source_sha256': 'a' * 64,
                    'source_cells': [op, 'fixture only']})
    return KMCOCatalog(records, source_digests={'core.md': 'a' * 64, 'domain.md': 'a' * 64})


class Circuits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        (self.repo / 'evidence.txt').write_text('evidence\n', encoding='utf-8')
        for args in [('init', '-q'), ('config', 'user.name', 'Fixture'),
                     ('config', 'user.email', 'fixture@example.invalid'), ('add', '.'),
                     ('commit', '-qm', 'fixture')]:
            subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)
        self.ctx = {'account_id': 'fixture-account', 'mission_id': 'fixture-mission',
                    'owner_node': 'future-node-42', 'task_id': 'fixture-task',
                    'workspace': str(self.repo), 'store': QueueStore(self.root / 'queue'),
                    'receipts_dir': self.root / 'receipts', 'state_dir': self.root / 'state',
                    'evidence': []}
        self.calls = []
        self.reg = ToolRegistry()
        self.reg.register(ToolSpec('fixture.source', 'fixture', {}, {}, 'read'),
                          lambda a, ctx: self.source())
        self.reg.register(ToolSpec('fixture.echo', 'fixture', {'text': 'str'}, {}, 'read'),
                          lambda a, ctx: self.echo(a))
        self.reg.register(ToolSpec('fixture.write', 'fixture', {}, {}, 'write'),
                          lambda a, ctx: self.source())
        c.register(self.reg)
        self.authorize()

    def tearDown(self):
        self.tmp.cleanup()

    def source(self):
        self.calls.append('source')
        return {'text': 'evidence'}

    def echo(self, args):
        self.calls.append('echo')
        return {'text': args['text']}

    def authorize(self):
        # Fixture trusted middleware binds exact requested arguments. Real
        # deployments must obtain these from their existing policy issuer.
        def producer(base, name, args):
            now = time.time()
            sha = c.identity(base)['source_sha']
            permissions = self.reg.names()
            principal = {'role': 'OWNER', 'permissions': permissions,
                'account_id': base['account_id'], 'mission_ids': [base['mission_id']],
                'task_ids': [base['task_id']]}
            auth = {'channel': 'ADMIN_V2', 'method': 'SESSION_CSRF', 'issuer': 'fixture-issuer',
                'authenticated': True, 'role': 'OWNER', 'principal_id': 'fixture-owner',
                'permissions': permissions, 'account_id': base['account_id'],
                'mission_id': base['mission_id'], 'task_ids': [base['task_id']],
                'issued_at': now, 'expires_at': now + 30}
            bindings = {name: {'qualified': True, 'account_id': base['account_id'],
                'mission_id': base['mission_id'], 'task_id': base['task_id'],
                'source_sha': sha, 'arguments': copy.deepcopy(args)}}
            approvals = {}
            spec = self.reg._specs[name]
            if spec.permission != 'read':
                bindings[name].update(write_authorized=True, approval_ref='fixture-approval')
                approvals['fixture-approval'] = {'verified': True, 'readback_confirmed': True,
                    'principal_id': 'fixture-owner', 'mission_id': base['mission_id'],
                    'task_id': base['task_id'], 'action': name.upper(), 'source_hash': sha,
                    'scope_sha256': c.digest(args), 'evidence_ref': 'fixture://explicit-approval',
                    'issued_at': now, 'expires_at': now + 30}
            return {'command_auth': auth, 'command_channels': {'ADMIN_V2': {
                'qualified': True, 'method': 'SESSION_CSRF', 'issuer': 'fixture-issuer',
                'principals': {'fixture-owner': principal}}},
                'tool_bindings': bindings, 'command_approvals': approvals}
        self.ctx['kmcp_step_context'] = producer

    def submit(self, value=None, request='request1'):
        compiled = c.compile_command({'request_id': request, 'command': 'produce evidence',
                                      'graph': value or graph()}, self.ctx, self.reg)
        return c.submit({'manifest': compiled['manifest']}, self.ctx, self.reg)

    def run_job(self, job, **kw):
        return c.run({**{k: job[k] for k in ('circuit_id', 'manifest_sha256')}, **kw}, self.ctx, self.reg)

    def assert_error(self, reason, fn):
        with self.assertRaises(ToolError) as error:
            fn()
        self.assertEqual(error.exception.reason, reason)

    def test_typed_dataflow_final_acceptance_and_original_audit(self):
        result = self.run_job(self.submit())
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['outputs'], {'answer': 'evidence'})
        self.assertEqual(self.calls, ['source', 'echo'])
        self.assertFalse(result['verified'])
        self.assertTrue(self.ctx['store'].read_events(result['circuit_id'])['chain_intact'])

    def test_restart_resumes_without_repeating_completed_nodes(self):
        job = self.submit()
        self.assertEqual(self.run_job(job, max_steps=1)['completed_nodes'], 1)
        self.ctx['store'] = QueueStore(self.root / 'queue')
        self.assertEqual(self.run_job(job)['status'], 'COMPLETED')
        self.run_job(job)
        self.assertEqual(self.calls, ['source', 'echo'])

    def test_duplicate_submission_and_conflicting_request(self):
        first = self.submit()
        self.assertFalse(self.submit()['created'])
        value = graph()
        value['acceptance'][0]['schema']['enum'] = ['changed']
        self.assert_error('CIRCUIT_IDEMPOTENCY_CONFLICT', lambda: self.submit(value))
        self.assertEqual(self.run_job(first)['status'], 'COMPLETED')

    def test_cycles_and_missing_dependencies_rejected_before_execution(self):
        value = graph()
        value['nodes'][0]['after'] = ['two']
        self.assert_error('CIRCUIT_DEPENDENCY_CYCLE', lambda: self.submit(value))
        value = graph()
        value['nodes'][1]['after'] = []
        self.assert_error('CIRCUIT_DEPENDENCY_INVALID', lambda: self.submit(value))
        self.assertEqual(self.calls, [])

    def test_port_type_and_undeclared_output_rejected(self):
        value = graph()
        value['nodes'][0]['result_schema']['properties']['text'] = {'type': 'integer'}
        self.assert_error('CIRCUIT_PORT_TYPE_MISMATCH', lambda: self.submit(value))
        value = graph()
        value['nodes'][1]['arguments']['text'] = {'$ref': 'one#/missing'}
        self.assert_error('CIRCUIT_OUTPUT_PORT_UNDECLARED', lambda: self.submit(value))

    def test_unknown_recursive_tools_and_unbounded_schema_denied(self):
        for name in ('shell', 'kmcp.circuit.run'):
            value = graph()
            value['nodes'][0]['tool'] = name
            self.assert_error('CIRCUIT_NODE_INVALID', lambda: self.submit(value))
        value = graph()
        value['nodes'][0]['result_schema']['$ref'] = 'https://example.invalid'
        self.assert_error('CIRCUIT_SCHEMA_UNSUPPORTED', lambda: self.submit(value))

    def test_schema_failure_blocks_downstream(self):
        self.reg._fns['fixture.source'] = lambda a, ctx: {'text': 7}
        result = self.run_job(self.submit())
        self.assertEqual(result['reason'], 'CIRCUIT_VALUE_SCHEMA_MISMATCH')
        self.assertEqual(result['nodes']['two']['status'], 'PENDING')

    def test_operation_rc_failure_is_not_success(self):
        self.reg._fns['fixture.source'] = lambda a, ctx: {'rc': 1}
        self.assertEqual(self.run_job(self.submit())['reason'], 'CIRCUIT_OPERATION_FAILED')

    def test_final_aggregate_contract_can_fail_after_all_nodes_done(self):
        value = graph()
        value['acceptance'][0]['schema']['enum'] = ['different']
        result = self.run_job(self.submit(value))
        self.assertEqual(result['completed_nodes'], 2)
        self.assertEqual(result['status'], 'FAILED')

    def test_no_authority_is_invented_from_a_manifest(self):
        self.ctx.pop('kmcp_step_context')
        result = self.run_job(self.submit())
        self.assertEqual(result['reason'], 'MCP_TOOL_SCOPE_DENIED')
        self.assertEqual(self.calls, [])

    def test_source_scope_and_approval_digest_changes_denied(self):
        job = self.submit()
        bad = dict(job, manifest_sha256='0' * 64)
        self.assert_error('CIRCUIT_APPROVED_DIGEST_MISMATCH', lambda: self.run_job(bad))
        self.ctx['account_id'] = 'other'
        self.assert_error('CIRCUIT_SCOPE_DENIED', lambda: c.report(job, self.ctx))
        self.ctx['account_id'] = 'fixture-account'
        subprocess.run(['git', '-C', str(self.repo), 'commit', '--allow-empty', '-qm', 'new'], check=True)
        self.assert_error('CIRCUIT_SOURCE_CHANGED', lambda: self.run_job(job))

    def test_checkpoint_corruption_and_ambiguous_interruption_never_replay(self):
        job = self.submit()
        body = c._load(job['circuit_id'], self.ctx)
        body['nodes']['one']['status'] = 'RUNNING'
        c._save(body, self.ctx)
        self.assertEqual(self.run_job(job)['reason'], 'CIRCUIT_OUTCOME_UNKNOWN_DO_NOT_REPLAY')
        self.assertEqual(self.calls, [])
        saved = self.ctx['store'].load_checkpoint(c._key(job['circuit_id']))
        saved['data']['body']['status'] = 'COMPLETED'
        self.ctx['store'].save_checkpoint(c._key(job['circuit_id']), c.SCHEMA, saved['data'])
        self.assert_error('CIRCUIT_CHECKPOINT_CORRUPT', lambda: c.report(job, self.ctx))

    def test_cancel_between_steps_has_no_new_dispatch(self):
        job = self.submit()
        self.run_job(job, max_steps=1)
        self.assertEqual(c.cancel(job, self.ctx)['status'], 'CANCEL_REQUESTED')
        self.assertEqual(self.run_job(job)['status'], 'CANCELLED')
        self.assertEqual(self.calls, ['source'])

    def test_readonly_poll_is_bounded_and_resumable(self):
        responses = iter([{'text': 'waiting'}, {'text': 'evidence'}])
        self.reg._fns['fixture.source'] = lambda a, ctx: next(responses)
        value = graph()
        value['nodes'][0].update(wait_for={'path': '/text', 'equals': 'evidence'}, max_polls=2)
        job = self.submit(value)
        self.assertEqual(self.run_job(job)['status'], 'WAITING')
        self.assertEqual(self.run_job(job)['status'], 'COMPLETED')

    def test_mutating_poll_requires_separate_operator_policy(self):
        value = graph()
        value['nodes'][0].update(tool='fixture.write', wait_for={'path': '/text', 'equals': 'evidence'}, max_polls=2)
        self.assertEqual(self.run_job(self.submit(value))['reason'], 'CIRCUIT_MUTATING_POLL_NOT_QUALIFIED')
        self.assertEqual(self.calls, [])

    def test_poll_exhaustion_and_invalid_budgets(self):
        value = graph()
        value['nodes'][0].update(wait_for={'path': '/text', 'equals': 'never'}, max_polls=1)
        self.assertEqual(self.run_job(self.submit(value))['reason'], 'CIRCUIT_POLL_BUDGET_EXHAUSTED')
        job = self.submit(request='another')
        self.assert_error('CIRCUIT_STEP_BUDGET_INVALID', lambda: self.run_job(job, max_steps=0))

    def test_command_only_uses_configured_planner_and_validates_output(self):
        seen = []
        self.ctx['kmcp_planner'] = lambda command, catalog: (seen.append(command) or graph())
        result = c.compile_command({'request_id': 'nl', 'command': 'แตกงานแล้วส่งหลักฐาน'}, self.ctx, self.reg)
        self.assertEqual(seen, ['แตกงานแล้วส่งหลักฐาน'])
        self.assertEqual(result['order'], ['one', 'two'])
        self.ctx['kmcp_planner'] = lambda *args: {'invented': True}
        self.assert_error('CIRCUIT_GRAPH_INVALID', lambda: c.compile_command(
            {'request_id': 'nl2', 'command': 'goal'}, self.ctx, self.reg))

    def test_command_without_planner_reports_blocker_no_fake_plan(self):
        self.assert_error('CIRCUIT_PLANNER_NOT_CONFIGURED', lambda: c.compile_command(
            {'request_id': 'nl', 'command': 'goal'}, self.ctx, self.reg))

    def test_work_units_compile_into_original_kmcw_and_save_a_real_plan(self):
        from coding_worker_v2.agent import pilot
        from coding_worker_v2.config import WorkerConfig
        config = {'schema': pilot.SCHEMA, 'account_id': self.ctx['account_id'],
            'mission_id': self.ctx['mission_id'], 'worker_id': self.ctx['owner_node'],
            'workspace': str(self.repo), 'runtime_base': str(self.root / 'runtime'),
            'allowed_paths': ['evidence.txt'], 'context_paths': ['evidence.txt'],
            'test_command': 'python3 -m unittest', 'provider': 'qwen-local'}
        file = self.root / 'pilot.json'
        file.write_text(json.dumps(config))
        self.ctx.update(pilot.load_context(file, worker_config=WorkerConfig(owner_node=self.ctx['owner_node'])))
        self.ctx['owner_node'] = config['worker_id']
        self.ctx['task_id'] = 'fixture-task'
        self.reg = build_default_registry(circuits=True)
        self.ctx['tool_registry'] = self.reg
        self.authorize()
        units = [{'task_id': name, 'goal': 'implement ' + name, 'deps': deps,
            'allowed_paths': ['evidence.txt'], 'acceptance': ['evidence is delivered'],
            'input_contract': ['source file'], 'output_contract': ['updated file'],
            'evidence_required': ['tests and independent QC']}
            for name, deps in [('t1', []), ('t2', ['t1'])]]
        compiled = c.compile_command({'request_id': 'coding', 'command': 'Implement the requested change',
            'work_units': units}, self.ctx, self.reg)
        self.assertEqual(compiled['planning_source'], 'KMCW_VALIDATED_WORK_UNITS')
        job = c.submit({'manifest': compiled['manifest']}, self.ctx, self.reg)
        first = self.run_job(job, max_steps=1)
        plan_id = first['nodes']['plan']['result']['plan_id']
        saved, _ = pilot._plan(plan_id, self.ctx)
        self.assertEqual([t.task_id for t in saved.tasks], ['t1', 't2'])
        self.assertEqual(saved.tasks[1].deps, ['t1'])
        self.assertTrue(all(t.execution_contract for t in saved.tasks))
        self.assertEqual(saved.owner_node, 'future-node-42')
        # Missing production capability/authority must remain a blocker, never
        # a pretend successful coding run in CI.
        self.ctx['kmcp_poll_tools'] = ('pilot_reconcile',)
        final = self.run_job(job)
        self.assertEqual(final['status'], 'BLOCKED')
        self.assertFalse(final['execution_complete'])

    def test_references_are_copied_not_mutated_and_false_is_not_zero(self):
        source = {'one': {'items': ['a']}}
        resolved = c.resolve({'$ref': 'one#/items'}, source)
        resolved.append('b')
        self.assertEqual(source['one']['items'], ['a'])
        self.assert_error('CIRCUIT_VALUE_SCHEMA_MISMATCH', lambda:
            c.validate_schema({'type': 'integer'}, False))

    def test_auth_is_refreshed_for_each_step_and_revocation_blocks(self):
        producer = self.ctx['kmcp_step_context']
        calls = []
        def refreshed(base, name, args):
            calls.append(name)
            value = producer(base, name, args)
            if name == 'fixture.echo':
                value['command_auth']['expires_at'] = time.time() - 1
            return value
        self.ctx['kmcp_step_context'] = refreshed
        result = self.run_job(self.submit())
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(calls, ['fixture.source', 'fixture.echo'])
        self.assertEqual(self.calls, ['source'])

    def test_one_manifest_approval_delegates_without_fabricating_child_approvals(self):
        job = self.submit()
        arguments = {k: job[k] for k in ('circuit_id', 'manifest_sha256')}
        producer = self.ctx.pop('kmcp_step_context')
        self.ctx.update(producer(self.ctx, 'kmcp.circuit.run', arguments))
        original_approvals = copy.deepcopy(self.ctx['command_approvals'])
        result = self.reg.call('kmcp.circuit.run', arguments, self.ctx, mcp=True)
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(self.calls, ['source', 'echo'])
        self.assertEqual(self.ctx['command_approvals'], original_approvals)

    def test_parent_approval_does_not_grant_unlisted_child_permission(self):
        job = self.submit()
        arguments = {k: job[k] for k in ('circuit_id', 'manifest_sha256')}
        producer = self.ctx.pop('kmcp_step_context')
        self.ctx.update(producer(self.ctx, 'kmcp.circuit.run', arguments))
        self.ctx['command_auth']['permissions'].remove('fixture.echo')
        # Remove from both sides: the parent remains qualified, but the child
        # tool is outside the explicitly delegated capability set.
        result = self.reg.call('kmcp.circuit.run', arguments, self.ctx, mcp=True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.calls, ['source'])

    def test_legacy_receipt_is_durable_and_not_independent_qc(self):
        from coding_worker_v2.receipts import read_receipt
        job = self.submit()
        result = self.run_job(job)
        receipt = result['receipt']
        self.assertIn('/history/', receipt['uri'])
        saved = read_receipt(Path(receipt['uri']))
        self.assertEqual(saved['status'], 'QC_PENDING')
        self.assertFalse(saved['output']['independent_verified'])
        self.assertEqual(self.run_job(job)['receipt'], receipt)

    def test_worker_code_upgrade_invalidates_old_circuit_even_if_tool_schema_matches(self):
        job = self.submit()
        with patch.object(c, 'runtime_digest', return_value='0' * 64):
            self.assert_error('CIRCUIT_RUNTIME_SOURCE_CHANGED', lambda: self.run_job(job))
        self.assertEqual(self.calls, [])

    def test_os_workspace_alias_has_one_memory_and_task_identity(self):
        from coding_worker_v2.agent.memory import canonical_repo, fingerprint_for
        alias = self.root / 'os-alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        sha = c.identity(self.ctx)['source_sha']
        self.assertEqual(canonical_repo({'repo': str(alias)}), str(self.repo))
        self.assertEqual(fingerprint_for('same goal', str(alias), sha),
                         fingerprint_for('same goal', str(self.repo), sha))

    def test_size_limit_and_constant_fake_output_denied(self):
        value = graph()
        value['outputs'] = {'answer': 'pretend'}
        self.assert_error('CIRCUIT_FINAL_ARTIFACT_REFERENCE_REQUIRED', lambda: self.submit(value))
        value = graph()
        value['outputs']['large'] = 'x' * (c.LIMIT + 1)
        self.assert_error('CIRCUIT_PAYLOAD_LIMIT', lambda: self.submit(value))

    def test_concurrent_runner_is_rejected(self):
        job = self.submit()
        with c._runner_lock(job['circuit_id'], self.ctx):
            self.assert_error('CIRCUIT_ALREADY_RUNNING', lambda: self.run_job(job))

    def test_actual_registry_and_kmco_dataflow(self):
        self.reg = build_default_registry(kmco_catalog=catalog(), circuits=True)
        self.assertEqual(len(self.reg.names()), 98)  # + six D06-D11 preview-only tools; no complete domain promotion
        self.authorize()
        value = {'nodes': [node('search', 'code_search', {'repo': str(self.repo), 'pattern': 'evidence'},
            obj(matches={'type': 'array', 'items': obj(file={'type': 'string'}, line={'type': 'string'}, text={'type': 'string'})})),
            node('prove', 'kmco.p01.evidence', {'file': 'evidence.txt', 'quote': {'$ref': 'search#/matches/0/text'}},
                {'type': 'object', 'required': ['output'], 'properties': {'output': {
                    'type': 'object', 'required': ['text'], 'properties': {'text': {'type': 'string'}}}}}, ['search'])],
            'outputs': {'answer': {'$ref': 'prove#/output/text'}},
            'acceptance': [{'path': '/answer', 'schema': {'type': 'string', 'enum': ['evidence']}}]}
        with patch.dict(os.environ, {'KMCO_PG_SERVICE': ''}):
            result = self.run_job(self.submit(value))
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertFalse(result['verified'])
        self.assertEqual(result['outputs']['answer'], 'evidence')

    def test_installable_config_is_relative_and_node_neutral(self):
        config = {'schema': 'KMCP_RUNTIME_V1', 'node_id': 'machine-of-the-future',
            'account_id': 'a', 'mission_id': 'm', 'task_id': 't', 'workspace': 'repo', 'runtime_base': 'runtime'}
        path = self.root / 'runtime.json'
        path.write_text(json.dumps(config))
        ctx, registry = load_runtime(path)
        self.assertEqual(ctx['owner_node'], 'machine-of-the-future')
        self.assertEqual(ctx['config'].owner_node, 'machine-of-the-future')
        self.assertEqual(ctx['workspace'], str(self.repo))
        self.assertEqual(len(registry.names()), 73)
        with patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(main(['--config', str(path), 'catalog']), 0)
            self.assertEqual(json.loads(out.getvalue())['command_count'], 73)

    def test_mcp_transport_exposes_circuits_without_changing_default_catalog(self):
        self.assertEqual(len(build_default_registry().names()), 67)
        self.ctx['tool_registry'] = build_default_registry(circuits=True)
        source = io.BytesIO(('\n'.join(json.dumps(r) for r in [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]) + '\n').encode())
        sink = io.StringIO()
        mcp_server.serve(self.ctx, source, sink)
        response = json.loads(sink.getvalue().splitlines()[1])['result']
        self.assertEqual(len(response['tools']), 73)
        tool = next(t for t in response['tools'] if t['name'] == 'kmcp.circuit.report')
        self.assertIn('outputSchema', tool)

    def test_real_stdio_command_submit_run_status_with_one_parent_approval(self):
        command = {'request_id': 'stdio', 'command': 'produce evidence', 'graph': graph()}
        compiled = c.compile_command(command, self.ctx, self.reg)
        manifest = compiled['manifest']
        circuit_id = 'circuit-' + c.digest([manifest[k] for k in
            ('account_id', 'mission_id', 'request_id')])[:24]
        calls = [('kmcp.command', command), ('kmcp.circuit.submit', {'manifest': manifest}),
                 ('kmcp.circuit.run', {'circuit_id': circuit_id, 'manifest_sha256': compiled['manifest_sha256']}),
                 ('kmcp.circuit.status', {'circuit_id': circuit_id})]
        producer = self.ctx.pop('kmcp_step_context')
        grants = iter(calls)
        def context_provider(base):
            name, arguments = next(grants)
            return producer(base, name, arguments)
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '2025-06-18'}}]
        for i, (name, arguments) in enumerate(calls, 2):
            requests.append({'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                             'params': {'name': name, 'arguments': arguments}})
        source = io.BytesIO(('\n'.join(json.dumps(r) for r in requests) + '\n').encode())
        sink = io.StringIO()
        self.ctx['tool_registry'] = self.reg
        mcp_server.serve(self.ctx, source, sink, context_provider=context_provider)
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertTrue(all(not r.get('result', {}).get('isError', False) for r in responses))
        report = responses[-1]['result']['structuredContent']
        self.assertEqual(report['status'], 'COMPLETED')
        self.assertEqual(report['outputs'], {'answer': 'evidence'})
        self.assertIsNotNone(report['receipt'])
        self.assertEqual(self.calls, ['source', 'echo'])


if __name__ == '__main__':
    unittest.main()
