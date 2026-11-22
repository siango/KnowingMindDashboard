"""Actual in-process KMCW MCP stdio transport tests; no fake production claims."""
import unittest
from coding_worker_v2.agent.local_mcp_acceptance import inspect_local
from coding_worker_v2.agent.tools import build_default_registry
from coding_worker_v2.tests.test_kmco_runtime import catalog


class LocalMCPReadbackTests(unittest.TestCase):
    def test_existing_local_transport_and_full_design_catalog(self):
        c=catalog()
        registry=build_default_registry(circuits=True,kmco_catalog=c)
        ctx={'tool_registry':registry,'kmco_catalog':c,'read_only':True,'evidence':[]}
        report=inspect_local(ctx,registry)
        self.assertEqual(report['initialize'],'PASS')
        self.assertEqual(report['tools_list'],'PASS')
        self.assertEqual(report['kmco']['design_routes_checked'],132)
        self.assertEqual(report['kmco']['status'],'DESIGN_VALIDATED_ONLY')
        self.assertEqual(report['kmco']['executable_routes_verified'],0)
        self.assertEqual(report['authenticated_http_origin'],'NOT_TESTED')
        self.assertFalse(report['production_acceptance'])
        self.assertEqual(report['tool_calls_executed'],0)

    def test_ssh_mcp_cli_forwards_config_without_unknown_argument_failure(self):
        from unittest import mock
        from coding_worker_v2.agent import ssh_cli
        with mock.patch("coding_worker_v2.agent.circuit_cli.main", return_value=0) as original:
            rc = ssh_cli.main(["mcp", "--config", "/existing/runtime.json", "local-check"])
            self.assertEqual(rc, 0)
            original.assert_called_once_with(["--config", "/existing/runtime.json", "local-check"])

    def test_missing_context_denied(self):
        with self.assertRaisesRegex(ValueError,'LOCAL_MCP_RUNTIME_REQUIRED'):
            inspect_local(None,None)


if __name__=='__main__':
    unittest.main()
