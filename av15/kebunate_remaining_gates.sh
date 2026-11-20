#!/usr/bin/env bash
# KEBUNATE AV15 — single-command remaining-gap live acceptance.
# Only measures missing cross-system gates; never manufactures authority,
# receipts, worker registrations, tool bindings, embeddings or mock results.
set -Eeuo pipefail
ROOT="$HOME/KnowingMind/workspaces/kmco-github-qc"
cd "$ROOT"
echo "MISSION=KEBUNATE_AV15_EXISTING_SYSTEM_CLOSEOUT"
echo "NODE=AV15"
echo "KNOWN_LOCAL_CORE_E2E=PASS_FROM_OPERATOR_2026-11-21_0336"
echo "SCOPE=REMAINING_KMNW_MCPV5_VECTOR_AND_INDEPENDENT_QC"
echo "GIT_HEAD=$(git rev-parse HEAD)"
echo "=== 1. EXISTING DATABASE TRUTH ==="
sudo -v
sudo -u postgres psql -X -w -d km_microops -v ON_ERROR_STOP=1 -Atqc "
 SELECT 'KMCO_CATALOG='||count(*) FROM circuit.registry;
 SELECT 'KMCO_BINDINGS='||count(*) FROM circuit.bindings;
 SELECT 'KMCO_RELATIONS='||count(*) FROM circuit.relations;
 SELECT 'KMCO_ROUTES='||count(*) FROM circuit.routes;
 SELECT 'KMCO_USAGE_EVENTS='||count(*) FROM circuit.usage_events;
 SELECT 'KMCO_CORE_CASES='||count(*) FROM circuit.core_memory;
 SELECT 'VECTOR_COLUMNS='||count(*) FROM pg_attribute a
  JOIN pg_class c ON c.oid=a.attrelid
  JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='circuit' AND a.attnum>0
    AND NOT a.attisdropped AND format_type(a.atttypid,a.atttypmod)
    LIKE 'vector(%';
"
echo "=== 2. KMNW AUTHENTICATED WORKER ROUTE DISCOVERY ==="
# Process or installed unit alone is not an ACK/lease/real execution proof.
systemctl list-unit-files --no-pager --no-legend 2>/dev/null |
 grep -i -E 'kmnw|knowingmind-network|network.worker' || true
systemctl --user list-unit-files --no-pager --no-legend 2>/dev/null |
 grep -i -E 'kmnw|knowingmind-network|network.worker' || true
pgrep -af '[k]mnw|[k]nowingmind-network-worker' || true
echo "KMNW_ACK_LEASE_AND_RESULT=NOT_VERIFIED_BY_THIS_SCRIPT"
echo "=== 3. EXISTING MCP V5 ORIGIN + CLOUDFLARE ==="
if systemctl is-active --quiet cloudflared.service; then
 echo "CLOUDFLARED_SYSTEMD=ACTIVE"
else
 echo "CLOUDFLARED_SYSTEMD=NOT_ACTIVE"
fi
python3 - <<'PY'
import socket, urllib.request, urllib.error
try:
    with socket.create_connection(("127.0.0.1",18856), timeout=2):
        print("MCP_ORIGIN_18856=TCP_ACCEPTING")
except OSError:
    print("MCP_ORIGIN_18856=NO_TCP_LISTENER")
try:
    req=urllib.request.Request("https://mcp.knowingmind.app/mcp",method="GET")
    with urllib.request.urlopen(req,timeout=7) as resp:
        print("MCP_EDGE_ANONYMOUS_HTTP="+str(resp.status))
except urllib.error.HTTPError as exc:
    print("MCP_EDGE_ANONYMOUS_HTTP="+str(exc.code))
except Exception:
    print("MCP_EDGE_ANONYMOUS_HTTP=UNREACHABLE")
PY
echo "MCPV5_AUTHENTICATED_CHATGPT_TOOL_EXECUTION=NOT_VERIFIED_BY_THIS_SCRIPT"
echo "=== 4. EXISTING WORKER AND MCP SOURCE CONTRACTS ==="
for name in \
 av15/coding_worker_v2/agent/mcp_server.py \
 av15/coding_worker_v2/agent/kmco_runtime.py \
 av15/coding_worker_v2/agent/kmco_core.py \
 av15/coding_worker_v2/agent/kmco_usage.py; do
 if [ -f "$name" ]; then echo "SOURCE_PRESENT=$name"; else echo "SOURCE_MISSING=$name"; fi
done
echo "=== 5. INDEPENDENT QC REFERENCE ==="
# A receipt must be authenticated and independently verified; do not
# promote tool event digests or local test PASS into accepted receipts.
echo "KMCW_LOCAL_P01=PREVIOUSLY_LIVE_VERIFIED"
echo "INDEPENDENT_QC_FOR_6_SYSTEM_CHAIN=NOT_VERIFIED"
echo "VECTOR_SEMANTIC_SEARCH=NOT_VERIFIED"
echo "FINAL=LOCAL_CORE_OPERATIONAL_FULL_SIX_SYSTEM_ACCEPTANCE_INCOMPLETE"
echo "EXIT_GATE=NOT_100_PERCENT"
