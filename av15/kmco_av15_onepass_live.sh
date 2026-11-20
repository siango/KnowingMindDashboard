#!/usr/bin/env bash
# KEBUNATE AV15 bounded integration acceptance — real DB event + KMCW KMCO.
# Reuses existing km_microops, existing KMCW registry, and P01 handler.
# Does not create a daemon, a second database, fake workers or MCP auth.
set -Eeuo pipefail
ROOT="$HOME/KnowingMind/workspaces/kmco-github-qc"
cd "$ROOT"
echo "MISSION=KEBUNATE_AV15_INTEGRATION"
echo "OWNER_NODE=AV15"
echo "MODE=REAL_LOCAL_EXECUTION_WITH_DB_READBACK"
echo "SOURCE_GIT_HEAD=$(git rev-parse HEAD)"
echo "OPERATOR=$(id -un)"
[ "$(id -un)" = siango ] || { echo "BLOCKED=UNEXPECTED_OS_USER"; exit 12; }
[ -f av15/coding_worker_v2/agent/kmco_usage.py ] || { echo "BLOCKED=KMCO_SOURCE_MISSING"; exit 13; }
[ -f av15/coding_worker_v2/agent/kmco_core.py ] || { echo "BLOCKED=CORE_SOURCE_MISSING"; exit 14; }
[ -f av15/coding_worker_v2/agent/kmco_runtime.py ] || { echo "BLOCKED=KMCW_REGISTRY_SOURCE_MISSING"; exit 15; }
[ -f "$HOME/KnowingMind/workspaces/microops-source/kmco-registry-candidate.json" ] || {
 echo "BLOCKED=CANONICAL_132_SNAPSHOT_MISSING"; exit 16;
}
echo "=== A. PYTHON DEPENDENCY ==="
if ! python3 -c 'import psycopg' >/dev/null 2>&1; then
  if apt-cache show python3-psycopg >/dev/null 2>&1; then
    sudo apt-get install -y python3-psycopg
  else
    echo "BLOCKED=OS_PYTHON_PSYCOPG_PACKAGE_UNAVAILABLE"
    echo "NO_UNPINNED_PIP_INSTALL_ATTEMPTED"
    exit 21
  fi
fi
python3 -c 'import psycopg; print("PSYCOPG_READY="+psycopg.__version__)'
echo "=== B. EXISTING DATABASE LEAST-PRIVILEGE PEER ROLE ==="
sudo -v
sudo -u postgres psql -X -w -d km_microops -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DO $$
BEGIN
 IF current_database() <> 'km_microops'
    OR to_regclass('circuit.registry') IS NULL
    OR to_regclass('circuit.usage_events') IS NULL
    OR to_regclass('circuit.core_memory') IS NULL THEN
    RAISE EXCEPTION 'KMCO_EXISTING_DB_CONTRACT_MISSING';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='siango') THEN
    EXECUTE 'CREATE ROLE siango LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='siango' AND rolsuper) THEN
    RAISE EXCEPTION 'KMCO_RUNTIME_ROLE_MUST_NOT_BE_SUPERUSER';
 END IF;
END $$;
GRANT CONNECT ON DATABASE km_microops TO siango;
GRANT USAGE ON SCHEMA circuit TO siango;
GRANT INSERT ON circuit.usage_events TO siango;
GRANT SELECT, INSERT, UPDATE ON circuit.core_memory TO siango;
GRANT USAGE ON SEQUENCE circuit.usage_events_id_seq TO siango;
GRANT USAGE ON SEQUENCE circuit.core_memory_case_id_seq TO siango;
COMMIT;
SQL
echo "=== C. EXISTING LIBPQ SERVICE FILE ==="
SERVICE_FILE="$HOME/.pg_service.conf"
if [ -L "$SERVICE_FILE" ]; then echo "BLOCKED=SERVICE_FILE_SYMLINK"; exit 31; fi
if [ -e "$SERVICE_FILE" ] && [ ! -f "$SERVICE_FILE" ]; then echo "BLOCKED=SERVICE_FILE_NOT_REGULAR"; exit 32; fi
if [ -f "$SERVICE_FILE" ] && grep -Eq '^\[kmco_runtime\][[:space:]]*$' "$SERVICE_FILE"; then
  echo "DB_SERVICE=EXISTING_ENTRY_REUSED"
else
  umask 077
  {
    printf '\n[kmco_runtime]\n'
    printf 'dbname=km_microops\n'
    printf 'user=siango\n'
    printf 'host=/var/run/postgresql\n'
    printf 'connect_timeout=3\n'
  } >> "$SERVICE_FILE"
  echo "DB_SERVICE=CONFIGURED_WITHOUT_PASSWORD"
fi
chmod 600 "$SERVICE_FILE"
export KMCO_PG_SERVICE=kmco_runtime
# Use an exact check independent of display formatting.
IDENTITY="$(psql -X -w -At -v ON_ERROR_STOP=1 'service=kmco_runtime' -c   "SELECT current_database() || ':' || current_user;")" || {
 echo "BLOCKED=LOCAL_PEER_DB_CONNECTION"; exit 33;
}
[ "$IDENTITY" = "km_microops:siango" ] || {
 echo "BLOCKED=DB_IDENTITY_MISMATCH"; exit 34;
}
echo "DB_IDENTITY=$IDENTITY"
echo "=== D. REAL KMCW -> KMCO P01 -> POSTGRES -> CORE RECALL ==="
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import sys, json, hashlib, os
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root/"av15"))
from coding_worker_v2.agent.tools import build_default_registry
from coding_worker_v2.agent.kmco_runtime import KMCOCatalog
from coding_worker_v2.agent.kmco_usage import _digest
import psycopg
catalog=KMCOCatalog.from_snapshot(Path.home()/"KnowingMind/workspaces/microops-source")
registry=build_default_registry(kmco_catalog=catalog)
required={"kmco.p01.evidence","kmco.core.recall","kmco.plan","kmco.inspect"}
if not required.issubset(set(registry.names())):
    raise SystemExit("BLOCKED=REQUIRED_KMCO_REGISTRY_TOOL_MISSING")
print("KMCO_CATALOG_COUNT=",catalog.summary()["operations"])
print("KMCW_REGISTERED_TOOLS=",sorted(required))
tool="kmco.p01.evidence"
args={"file":"av15/coding_worker_v2/agent/kmco_p01.py",
      "quote":"KMCO P01 evidence circuit: five explicit deterministic steps."}
ctx={"workspace":str(root), "read_only":True,
     "mission_id":"G0-G1-MINIMUM-SELLABLE-DEMO",
     "task_id":"kmco-av15-integrated-p01","evidence":[]}
result=registry.call(tool,args,ctx)
expected=[f"P01-M{i:02d}" for i in range(1,6)]
actual=hashlib.sha256((root/args["file"]).read_bytes()).hexdigest()
if result.get("steps_completed") != expected or result["output"]["citation"]["sha256"] != actual:
    raise SystemExit("BLOCKED=P01_SOURCE_OUTPUT_MISMATCH")
print("P01_REAL_EXECUTION=PASS",flush=True)
print("P01_COMPLETED_STEPS=",result["steps_completed"])
print("SOURCE_SHA256=",actual)
with psycopg.connect("service=kmco_runtime",connect_timeout=3) as con:
    with con.cursor() as cur:
        cur.execute("""SELECT operation, success, result_sha256, args_sha256
                       FROM circuit.usage_events
                       WHERE mission_id=%s AND task_id=%s AND operation=%s
                       ORDER BY id DESC LIMIT 1""",
                   (ctx["mission_id"],ctx["task_id"],tool))
        row=cur.fetchone()
        event=next((x for x in ctx["evidence"] if x.get("event")=="tool_called" and x.get("tool")==tool and x.get("ok")),None)
        if not row or not row[1] or not event or row[2] != event["result_sha256"] or row[3]!=_digest(args):
            raise SystemExit("BLOCKED=KMCO_USAGE_EXACT_READBACK_MISMATCH")
        cur.execute("""SELECT result_state, dukkha, samudaya, nirodha, magga, attempt_count
                       FROM circuit.core_memory
                       WHERE mission_id=%s AND task_id=%s
                         AND operation=%s AND args_sha256=%s""",
                    (ctx["mission_id"],ctx["task_id"],tool,_digest(args)))
        m=cur.fetchone()
        if not m or m[0]!="EXECUTED_UNVERIFIED" or m[4].get("result_sha256")!=event["result_sha256"] or m[3].get("verified") is not False:
            raise SystemExit("BLOCKED=KMCO_CORE_EXACT_READBACK_MISMATCH")
        print("DB_USAGE_EXACT_READBACK=PASS")
        print("DB_CORE_FOUR_PART_READBACK=PASS")
        print("DB_RECORDED_ATTEMPTS=",m[5])
memory=registry.call("kmco.core.recall",{"limit":10},ctx)
if memory.get("state")!="DERIVED_UNVERIFIED" or not any(
     c.get("operation")==tool for c in memory.get("cases",[])):
    raise SystemExit("BLOCKED=KMCW_CORE_RETRIEVAL_MISMATCH")
print("KMCW_CORE_RECALL=PASS")
print("ARIYASACCA_CAUSE=NOT_DIAGNOSED")
print("INDEPENDENT_QC=NOT_RUN")
print("VERIFIED_RECEIPT=NOT_PRODUCED")
PY
echo "LOCAL_CORE_E2E=PASS"
echo "=== E. CROSS-SYSTEM ACCEPTANCE (NO FAKE PASS) ==="
if pgrep -af '[k]mnw' >/dev/null 2>&1; then
  echo "KMNW_PROCESS=CANDIDATE_FOUND"
else
  echo "KMNW_PROCESS=NOT_FOUND"
fi
if pgrep -af '[c]loudflared.*tunnel' >/dev/null 2>&1; then
  echo "CLOUDFLARE_TUNNEL_PROCESS=FOUND"
else
  echo "CLOUDFLARE_TUNNEL_PROCESS=NOT_FOUND"
fi
echo "KMNW_ACK_LEASE_EXECUTION=NOT_LIVE_VERIFIED"
echo "MCPV5_CHATGPT_TOOL_CALL=NOT_LIVE_VERIFIED"
echo "VECTOR_SEARCH_132=NOT_LIVE_VERIFIED"
echo "INDEPENDENT_RECEIPT=NOT_PRODUCED"
echo "FINAL=LOCAL_CORE_E2E_PASS_FULL_6_SYSTEM_ACCEPTANCE_BLOCKED"
