#!/usr/bin/env bash
# KEBUNATE G1: deploy secure MCP source and test fail-closed boundaries.
# Does not overwrite existing AV15 files, launch public services, create tokens,
# invent Admin credentials, grant KMCW execution, or claim independent receipts.
set -euo pipefail
ROOT="${HOME}/KnowingMind/workspaces/kmco-github-qc"
PY="${HOME}/KnowingMind/.venvs/mcpv5-http/bin/python"
BRANCH="refs/heads/feature/km-coding-worker-v3-handoff"
cd "$ROOT"
echo "MISSION=G0-G1-MINIMUM-SELLABLE-DEMO"
echo "STEP=AUTHENTICATED_BINDING"
git fetch https://github.com/siango/KnowingMindDashboard.git "$BRANCH"
for path in av15/mcpv5_kmcw_authenticated.py av15/test_mcpv5_kmcw_authenticated.py; do
  tmp="$(mktemp)"
  git show "FETCH_HEAD:$path" > "$tmp"
  if [ -e "$path" ]; then
    if ! cmp -s "$path" "$tmp"; then
      rm -f "$tmp"
      echo "BLOCKED_LOCAL_FILE_DIFFERS=$path"
      exit 2
    fi
    echo "ALREADY_PRESENT=$path"
  else
    install -m 0644 "$tmp" "$path"
    echo "SOURCE_INSTALLED=$path"
  fi
  rm -f "$tmp"
done
echo "SOURCE_SYNTAX"
"$PY" -m py_compile av15/mcpv5_kmcw_authenticated.py av15/test_mcpv5_kmcw_authenticated.py
echo "SECURITY_TESTS"
PYTHONPATH="$ROOT:$ROOT/av15" PYTHONDONTWRITEBYTECODE=1 "$PY" \
  -m pytest -q -p no:cacheprovider \
  av15/test_mcpv5_secure_http_readback.py \
  av15/test_mcpv5_kmcw_authenticated.py
echo "ORIGIN_LISTENER"
ss -ltn '( sport = :18856 )'
echo "SOURCE_SECURITY_QC_PASS=ONLY_IF_TESTS_PASS"
if [ -n "${CREDENTIALS_DIRECTORY:-}" ] && [ -r "${CREDENTIALS_DIRECTORY}/km-ai-admin.session-cookie" ]; then
  echo "ADMIN_RUNTIME_CREDENTIAL=AVAILABLE_TO_SHELL"
else
  echo "ADMIN_RUNTIME_CREDENTIAL=NOT_AVAILABLE_TO_SHELL"
fi
echo "READ_EXECUTION_GATE=REQUIRES_SIGNED_JWT_MATCHING_ADMIN_IDENTITY_AND_ISSUER_GRANT"
echo "VERIFIED_RECEIPT=NOT_PRODUCED"
echo "CHATGPT_PLUGIN=NOT_CONNECTED_BY_THIS_SCRIPT"
