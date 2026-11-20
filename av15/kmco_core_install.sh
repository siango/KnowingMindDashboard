#!/usr/bin/env bash
# KMCW + KMCO original process only. No second daemon, database or MCP.
set -euo pipefail
ROOT="${HOME}/KnowingMind/workspaces/kmco-github-qc"
cd "$ROOT"
echo "MISSION=KMCO_KNOW_REMEMBER_DO_ARIYASACCA"
echo "NODE=AV15"
echo "DATABASE=km_microops"
echo "SOURCE_PIN=$(git rev-parse FETCH_HEAD)"

FILES=(
 av15/coding_worker_v2/agent/tools.py
 av15/coding_worker_v2/agent/kmco_runtime.py
 av15/coding_worker_v2/agent/kmco_usage.py
 av15/coding_worker_v2/agent/kmco_core.py
 av15/coding_worker_v2/tests/test_kmco_runtime.py
 av15/coding_worker_v2/tests/test_kmco_usage.py
 av15/coding_worker_v2/tests/test_kmco_core.py
 av15/sql/kmco_usage_events.sql
 av15/sql/kmco_core_memory.sql
)
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
idx=0
for file in "${FILES[@]}"; do
  idx=$((idx+1))
  git show "FETCH_HEAD:$file" > "$TEMP/$idx" || {
    echo "BLOCKED=GITHUB_SOURCE_MISSING:$file"; exit 2;
  }
  if [ -L "$file" ]; then
    echo "BLOCKED=LOCAL_SYMLINK:$file"; exit 3
  fi
  if [ -e "$file" ] && [ ! -f "$file" ]; then
    echo "BLOCKED=LOCAL_PATH_NOT_FILE:$file"; exit 3
  fi
  if [ -f "$file" ] && ! cmp -s "$TEMP/$idx" "$file"; then
    # Never overwrite locally modified source, including any untracked file.
    local_sha="$(git hash-object "$file")"
    # Pre-existing committed source or the prior P01 binding is recognized.
    prior_sha=""
    case "$file" in
       av15/coding_worker_v2/agent/kmco_runtime.py)
          prior_sha="503b5cbc8636c179f29d4abec5e3f79fc82f6165" ;;
       av15/coding_worker_v2/tests/test_kmco_runtime.py)
          # Known prior P01 permanent-binding test, verified in GitHub.
          prior_sha="52dd4b66a977c2ed5103c5cfec9359533a3bf412" ;;
    esac
    if ! git cat-file -e "HEAD:$file" 2>/dev/null; then
      echo "BLOCKED=LOCAL_FILE_NOT_TRACKED:$file"; exit 4
    fi
    tracked_sha="$(git rev-parse "HEAD:$file")"
    if [ "$local_sha" != "$tracked_sha" ] && [ "$local_sha" != "$prior_sha" ]; then
      echo "BLOCKED=LOCAL_MODIFICATIONS:$file"; exit 4
    fi
  fi
done

idx=0
for file in "${FILES[@]}"; do
  idx=$((idx+1))
  mkdir -p "$(dirname "$file")"
  install -m 644 "$TEMP/$idx" "$file"
  echo "SOURCE_INSTALLED=$file"
done

echo "=== KMCW KMCO SOURCE QC ==="
(
 cd av15
 env -u KMCO_PG_SERVICE PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
   coding_worker_v2.tests.test_kmco_core \
   coding_worker_v2.tests.test_kmco_usage \
   coding_worker_v2.tests.test_kmco_runtime \
   coding_worker_v2.tests.test_kmco_p01 \
   coding_worker_v2.tests.test_kmco_p02
)
echo "SOURCE_QC=PASS"

echo "=== EXISTING DATABASE GUARD ==="
sudo -v
check="$(sudo -u postgres psql -X -w -At -d km_microops -v ON_ERROR_STOP=1 -c \
 "SELECT CASE WHEN current_database()='km_microops'
   AND to_regnamespace('circuit') IS NOT NULL
   AND to_regclass('circuit.registry') IS NOT NULL
   AND to_regclass('circuit.bindings') IS NOT NULL
   AND to_regclass('circuit.relations') IS NOT NULL
   AND to_regclass('circuit.routes') IS NOT NULL
   THEN 'EXISTING_CIRCUIT_OK' ELSE 'BLOCKED' END")"
[ "$check" = "EXISTING_CIRCUIT_OK" ] || {
 echo "BLOCKED=CIRCUIT_SCHEMA_OR_TABLES_MISSING"; exit 5;
}
echo "DB_REUSE=EXISTING_CIRCUIT_OK"
sudo -u postgres psql -X -w -d km_microops -v ON_ERROR_STOP=1 \
  -f av15/sql/kmco_usage_events.sql
sudo -u postgres psql -X -w -d km_microops -v ON_ERROR_STOP=1 \
  -f av15/sql/kmco_core_memory.sql
sudo -u postgres psql -X -w -d km_microops -v ON_ERROR_STOP=1 -Atqc \
 "SELECT 'USAGE_EVENTS='||COALESCE(to_regclass('circuit.usage_events')::text,'MISSING');
  SELECT 'CORE_MEMORY='||COALESCE(to_regclass('circuit.core_memory')::text,'MISSING');
  SELECT 'CORE_CASES='||count(*) FROM circuit.core_memory;"
echo "DB_SCHEMA_QC=PASS"

echo "=== AUTOMATIC MEMORY ACTIVATION ==="
if [ -z "${KMCO_PG_SERVICE:-}" ]; then
 echo "RUNTIME=EXISTING_KMCW_TOOLREGISTRY"
 echo "AUTOMATIC_MEMORY=BLOCKED_LIBPQ_SERVICE_NOT_CONFIGURED"
elif ! PYTHONDONTWRITEBYTECODE=1 python3 -c 'import psycopg' >/dev/null 2>&1; then
 echo "AUTOMATIC_MEMORY=BLOCKED_PSYCOPG_DRIVER_MISSING"
else
 echo "AUTOMATIC_MEMORY=CONFIGURED_LIVE_NOT_VERIFIED"
fi
echo "VECTOR_EMBEDDING=NOT_CREATED_NO_MODEL_DIMENSIONS"
echo "DHAMMA_CANON=UNTOUCHED"
echo "INDEPENDENT_QC=NOT_RUN"
echo "RESULT=SOURCE_AND_EXISTING_DB_SCHEMA_INSTALLED"
