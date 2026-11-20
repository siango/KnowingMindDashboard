#!/usr/bin/env bash
# Opt-in, non-destructive KMCO usage recorder deployment to existing AV15 tree.
# Fetch remotely first. Never git pull/reset and never overwrite a dirty file.
set -euo pipefail
ROOT="${HOME}/KnowingMind/workspaces/kmco-github-qc"
cd "$ROOT"
echo "MISSION=KMCO_AUTOMATIC_POSTGRES_AUDIT"
echo "DATABASE=km_microops"
echo "SOURCE_REF=$(git rev-parse FETCH_HEAD 2>/dev/null || echo UNAVAILABLE)"
FILES=(
  "av15/coding_worker_v2/agent/tools.py"
  "av15/coding_worker_v2/agent/kmco_usage.py"
  "av15/coding_worker_v2/tests/test_kmco_usage.py"
  "av15/sql/kmco_usage_events.sql"
)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
i=0
for f in "${FILES[@]}"; do
  i=$((i+1))
  git show "FETCH_HEAD:$f" > "$TMP/$i" || {
    echo "BLOCKED=REMOTE_SOURCE_MISSING:$f"; exit 2;
  }
  if [ -L "$f" ]; then
    echo "BLOCKED=LOCAL_SYMLINK:$f"; exit 3;
  fi
  if [ -e "$f" ]; then
    [ -f "$f" ] || { echo "BLOCKED=LOCAL_NOT_REGULAR:$f"; exit 4; }
    if ! cmp -s "$TMP/$i" "$f"; then
      # A tracked pristine file may be replaced; owner-edited ones may not.
      if ! git cat-file -e "HEAD:$f" 2>/dev/null ||
         ! cmp -s "$f" <(git show "HEAD:$f"); then
        echo "BLOCKED=LOCAL_MODIFICATIONS:$f"
        exit 5
      fi
    fi
  fi
done
i=0
for f in "${FILES[@]}"; do
  i=$((i+1))
  mkdir -p "$(dirname "$f")"
  install -m 644 "$TMP/$i" "$f"
  echo "SOURCE_INSTALLED=$f"
done
echo "=== SOURCE QC ==="
(
 cd av15
 env -u KMCO_PG_SERVICE PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
    coding_worker_v2.tests.test_kmco_usage \
    coding_worker_v2.tests.test_kmco_runtime \
    coding_worker_v2.tests.test_kmco_p01
)
echo "SOURCE_QC=PASS"
echo "=== EXISTING DB MIGRATION ==="
echo "Creates only circuit.usage_events inside existing km_microops."
sudo -v
sudo -u postgres psql -X -w -d km_microops -v ON_ERROR_STOP=1 \
    -f "av15/sql/kmco_usage_events.sql"
sudo -u postgres psql -X -w -d km_microops -v ON_ERROR_STOP=1 -Atqc \
  "SELECT 'EVENT_TABLE='||to_regclass('circuit.usage_events')::text;
   SELECT 'RECORDED_ROWS='||count(*) FROM circuit.usage_events;"
echo "=== ACTIVATION GATE ==="
if [ -z "${KMCO_PG_SERVICE:-}" ]; then
  echo "AUTO_PERSISTENCE=NOT_ACTIVE"
  echo "BLOCKER=KMCW_LIBPQ_SERVICE_CREDENTIAL_NOT_CONFIGURED"
else
  if PYTHONDONTWRITEBYTECODE=1 python3 -c 'import psycopg' >/dev/null 2>&1; then
    echo "AUTO_PERSISTENCE=CONFIGURED_NOT_LIVE_VERIFIED"
    echo "NEXT=TEST_SINGLE_REAL_KMCO_CALL_WITH_EXISTING_DB_SERVICE"
  else
    echo "AUTO_PERSISTENCE=NOT_ACTIVE"
    echo "BLOCKER=PYTHON_PSYCOPG_NOT_INSTALLED"
  fi
fi
echo "INDEPENDENT_RECEIPT=NOT_PRODUCED"
echo "DEPLOY_QC=PASS_SOURCE_AND_SCHEMA_ONLY"
