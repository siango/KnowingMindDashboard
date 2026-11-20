#!/usr/bin/env bash
# Repairs actual 05:01 blocker: system Python cannot import MCP SDK.
# Reuse existing SDK Python; fallback installs packages into one isolated env.
set -Eeuo pipefail
cd "$HOME/KnowingMind/workspaces/kmco-github-qc"
echo "MISSION=KEBUNATE_FLOW_AV15_DEPENDENCY_RECOVERY"
echo "EVIDENCE_BLOCKER=ModuleNotFoundError:mcp"
echo "SOURCE_PIN=$(git rev-parse FETCH_HEAD)"
pick=""
if python3 -c 'from mcp.server.mcpserver import MCPServer;import jwt,uvicorn,psycopg,pytest' >/dev/null 2>&1; then
  pick="$(command -v python3)"
else
  while IFS= read -r p; do
    if [ -x "$p" ] && "$p" -c 'from mcp.server.mcpserver import MCPServer;import jwt,uvicorn,psycopg,pytest' >/dev/null 2>&1; then
      pick="$p"; break
    fi
  done < <(find "$HOME/KnowingMind" "$HOME/.local" /opt -maxdepth 7 -type f -name python -path '*/bin/python' 2>/dev/null | head -60)
fi
if [ -z "$pick" ]; then
  ENVROOT="$HOME/KnowingMind/.venv-mcpv5"
  if [ -L "$ENVROOT" ]; then echo "BLOCKED=DEPENDENCY_ENV_SYMLINK";exit 4;fi
  if [ ! -f "$ENVROOT/pyvenv.cfg" ]; then
    python3 -m venv --system-site-packages "$ENVROOT" || { echo "BLOCKED=VENV_NOT_AVAILABLE";exit 5; }
  fi
  pick="$ENVROOT/bin/python"
  echo "DEPENDENCY_ENV=ISOLATED"
  "$pick" -m pip install --disable-pip-version-check --no-input \
    'mcp==2.3.0' 'PyJWT>=2.8,<3' 'uvicorn>=0.30,<1' \
    'cryptography>=42' 'psycopg>=3.1,<4' 'pytest>=8,<10' || { echo "BLOCKED=PYTHON_DEPENDENCY_INSTALL_FAILED";exit 6; }
else
  echo "DEPENDENCY_ENV=EXISTING_REUSED"
fi
"$pick" -c 'from mcp.server.mcpserver import MCPServer;import jwt,uvicorn,psycopg,pytest' || { echo "BLOCKED=SDK_IMPORT";exit 7; }
echo "SDK_IMPORT=PASS"
# Existing installer uses python3 and command -v python3; choose by PATH.
BIN="$(dirname "$pick")"
if [ ! -x "$BIN/python3" ]; then
  # Fallback for existing environments exposing only python.
  echo "BLOCKED=EXISTING_ENV_MISSING_PYTHON3_SYMLINK";exit 8
fi
export PATH="$BIN:$PATH"
echo "RUNNING=EXISTING_RECONCILIATION_SCRIPT"
bash <(git show FETCH_HEAD:av15/kebunate_flow_reconcile_systemd.sh)
echo "RECOVERY_EXIT_CODE=0"
