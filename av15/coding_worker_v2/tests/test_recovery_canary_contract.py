"""Source-contract regression only; no provider calls or native acceptance."""
import copy
import importlib.util
from pathlib import Path
import unittest
from coding_worker_v2.agent import circuits
from coding_worker_v2.agent.tools import ToolError


class RecoveryCanaryContract(unittest.TestCase):
    def setUp(self):
        script=Path(__file__).resolve().parents[3]/'scripts/host/accept-runtime-recovery.py'
        spec=importlib.util.spec_from_file_location('recovery_acceptance_contract',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.graph=module.recovery_canary_graph('fixture-model','README.md','fixture quote')
        class Contracts:
            def command_contract(self):
                return {'commands':[
                    {'name':'kmco.p01.evidence','required':{'file':'str','quote':'str'},'optional':{}},
                    {'name':'cf.ai.run','required':{'model':'str','prompt':'str'},'optional':{'max_tokens':'int'}},
                    {'name':'kmco.core.recall','required':{},'optional':{'limit':'int'}}]}
        self.contracts=Contracts()

    def test_acceptance_graph_declares_every_actual_output_port(self):
        self.assertEqual(circuits.validate_graph(self.graph,self.contracts),['p01','ai','memory'])
        schema=self.graph['nodes'][0]['result_schema']
        self.assertEqual(circuits._port_schema(schema,'/output/state')['enum'],['EXACT_QUOTE_VERIFIED'])

    def test_previous_acceptance_graph_is_rejected_before_any_execution(self):
        graph=copy.deepcopy(self.graph)
        graph['nodes'][0]['result_schema']['properties']['output']={'type':'object'}
        with self.assertRaises(ToolError) as raised:circuits.validate_graph(graph,self.contracts)
        self.assertEqual(raised.exception.reason,'CIRCUIT_OUTPUT_PORT_UNDECLARED')


if __name__=='__main__':unittest.main()
