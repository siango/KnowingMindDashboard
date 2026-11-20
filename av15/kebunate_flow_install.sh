#!/usr/bin/env bash
# AV15: deploy Flow Studio INTO existing MCP V5 authenticated origin.
# No new tunnel, no public HTTP server, no production write or fake telemetry.
set -Eeuo pipefail
ROOT="$HOME/KnowingMind/workspaces/kmco-github-qc"
cd "$ROOT"
echo "MISSION=KEBUNATE_FLOW_STUDIO_AV15"
echo "SOURCE_PIN=$(git rev-parse FETCH_HEAD)"
FILES=(
 av15/mcpv5_secure_http_readback.py
 av15/mcpv5_kmcw_authenticated.py
 av15/kebunate_flow_ui.py
 av15/kebunate_flow.html
 av15/test_kebunate_flow_ui.py
)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
i=0
for file in "${FILES[@]}"; do
 i=$((i+1))
 git show "FETCH_HEAD:$file" > "$TMP/$i" || { echo "BLOCKED=SOURCE_MISSING:$file"; exit 2; }
 if [ -L "$file" ]; then echo "BLOCKED=SYMLINK:$file"; exit 3; fi
 if [ -e "$file" ] && [ ! -f "$file" ]; then echo "BLOCKED=UNEXPECTED_FILE_TYPE:$file"; exit 4; fi
 if [ -f "$file" ] && ! cmp -s "$TMP/$i" "$file"; then
  if ! git cat-file -e "HEAD:$file" 2>/dev/null ||
     ! cmp -s "$file" <(git show "HEAD:$file"); then
     echo "BLOCKED=LOCAL_MODIFICATIONS:$file"
     exit 5
  fi
 fi
done
i=0
for file in "${FILES[@]}"; do
 i=$((i+1))
 mkdir -p "$(dirname "$file")"
 install -m 644 "$TMP/$i" "$file"
 echo "SOURCE_INSTALLED=$file"
done
echo "=== SOURCE QC ==="
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
 av15.test_kebunate_flow_ui \
 av15.test_mcpv5_secure_http_readback \
 av15.test_mcpv5_kmcw_authenticated
echo "SOURCE_QC=PASS"
echo "=== EXISTING ACCESS CREDENTIAL SUB CHECK ==="
# Never output/store Access token. JWKS verifies the token before deriving sub.
if [ -n "${KEBUNATE_ACCESS_SUB:-}" ]; then
 SUBJECT="$KEBUNATE_ACCESS_SUB"
else
 SUBJECT="$(PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import jwt,subprocess
from av15.mcpv5_secure_http_readback import ISSUER,AUDIENCE
try:
    p=subprocess.run(["cloudflared","access","token","-app",
                      "https://mcp.knowingmind.app"],
                     capture_output=True,text=True,timeout=20)
    if p.returncode:raise ValueError()
    token=p.stdout.strip()
    key=jwt.PyJWKClient(ISSUER+"/cdn-cgi/access/certs",timeout=5).get_signing_key_from_jwt(token)
    claims=jwt.decode(token,key.key,algorithms=["RS256"],audience=AUDIENCE,
                      issuer=ISSUER,options={"require":["sub","exp","iat","iss","aud"]})
    if claims.get("type")!="app" or not claims.get("sub"):raise ValueError()
    print(claims["sub"])
except Exception:
    raise SystemExit(2)
PY
)" || { echo "BLOCKED=ACCESS_LOGIN_OR_SIGNED_TOKEN_UNAVAILABLE"; exit 6; }
fi
if [ -z "$SUBJECT" ]; then echo "BLOCKED=ACCESS_SUB_MISSING"; exit 6; fi
echo "ACCESS_SUBJECT=RESOLVED_NOT_PRINTED"
echo "=== DB SERVICE CHECK ==="
if ! KMCO_PG_SERVICE=kmco_runtime PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from av15.kebunate_flow_ui import _pg_snapshot
s=_pg_snapshot()
print("DB_STATE="+s["state"])
if s["state"]!="DB_READBACK":raise SystemExit(3)
print("DB_USAGE_EVENTS="+str(s["tables"].get("usage_events")))
print("DB_CORE_CASES="+str(s["tables"].get("core_memory")))
PY
then
  echo "BLOCKED=FLOW_DB_READBACK_UNAVAILABLE_OR_INSUFFICIENT_SELECT"
  echo "NOTE=Existing siango role needs SELECT on circuit.registry,bindings,relations,routes"
  exit 7
fi
echo "=== MCP ORIGIN ==="
if (echo >/dev/tcp/127.0.0.1/18856) >/dev/null 2>&1; then
 echo "BLOCKED=EXISTING_MCP_ORIGIN_ALREADY_LISTENING"
 echo "NEXT=RESTART_EXISTING_ORIGIN_WITH_UPDATED_SOURCE_AFTER_REVIEW"
 exit 8
fi
# Reuse the existing MCP V5 server module; no second port or tunnel.
if ! systemctl --user show-environment >/dev/null 2>&1; then
 echo "BLOCKED=USER_SYSTEMD_UNAVAILABLE"
 echo "NEXT=RUN_EXISTING_MODULE_IN_A_PERSISTENT_SESSION"
 exit 9
fi
umask 077
ENV_DIR="$HOME/.config/kebunate"
mkdir -p "$ENV_DIR" "$HOME/.config/systemd/user"
CONF="$ENV_DIR/mcpv5-flow.env"
if [ -e "$CONF" ] && [ ! -f "$CONF" ]; then echo "BLOCKED=ENV_CONFIG_NOT_REGULAR"; exit 10; fi
if [ -f "$CONF" ]; then
 echo "BLOCKED=EXISTING_MCP_ENV_FILE_PRESERVED"
 echo "NEXT=REVIEW_EXISTING_SERVICE_CONFIG_BEFORE_ACTIVATION"
 exit 11
fi
case "$SUBJECT" in *[!a-zA-Z0-9._:@-]*|"") echo "BLOCKED=SUBJECT_FORMAT"; exit 12;; esac
printf 'KEBUNATE_HTTP_READBACK_CONFIRM=YES\nKEBUNATE_ACCESS_SUB=%s\nKMCO_PG_SERVICE=kmco_runtime\n' "$SUBJECT" > "$CONF"
chmod 600 "$CONF"
UNIT="$HOME/.config/systemd/user/kebunate-mcpv5-readback.service"
if [ -e "$UNIT" ]; then
 echo "BLOCKED=EXISTING_MCP_SERVICE_PRESERVED"
 exit 13
fi
PYTHON="$(command -v python3)"
cat > "$UNIT" <<EOF
[Unit]
Description=Existing KEBUNATE MCP V5 authenticated origin and Flow Studio
After=network-online.target
[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=$CONF
ExecStart=$PYTHON -m av15.mcpv5_secure_http_readback
Restart=on-failure
RestartSec=4
NoNewPrivileges=yes
[Install]
WantedBy=default.target
EOF
chmod 600 "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now kebunate-mcpv5-readback.service
sleep 2
if ! systemctl --user is-active --quiet kebunate-mcpv5-readback.service; then
 echo "BLOCKED=MCP_ORIGIN_SERVICE_FAILED"
 exit 14
fi
# Anonymous access must always be denied, even from loopback.
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:18856/flow)"
[ "$CODE" = 401 ] || { echo "BLOCKED=FLOW_ANONYMOUS_DENIAL_FAILED:$CODE"; exit 15; }
echo "FLOW_ANONYMOUS_HTTP=401"
echo "MCP_ORIGIN_SYSTEMD=ACTIVE"
echo "FLOW_STUDIO_ORIGIN=RUNNING_AUTH_REQUIRED"
echo "FLOW_URL=https://mcp.knowingmind.app/flow"
echo "REMOTE_AUTHENTICATED_BROWSER=NOT_TESTED"
echo "ADMIN_V2_DEPLOY=SEPARATE_EXISTING_ADMIN_RELEASE_REQUIRED"
echo "RESULT=AV15_FLOW_ORIGIN_READY_PENDING_REMOTE_ACCEPTANCE"
