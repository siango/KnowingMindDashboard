#!/usr/bin/env bash
# AV15 targeted repair: preserve existing MCP V5 source, add only protected Flow route.
# Uses system-wide systemd instead of unavailable --user bus; do NOT start a second MCP origin.
set -Eeuo pipefail
cd "$HOME/KnowingMind/workspaces/kmco-github-qc"
echo "MISSION=KEBUNATE_FLOW_STUDIO_AV15"
echo "MODE=RECONCILE_EXISTING_MCP_ORIGIN"
echo "SOURCE_PIN=$(git rev-parse FETCH_HEAD)"
[ "$(id -un)" = siango ] || { echo "BLOCKED=EXPECTED_AV15_SIANGO"; exit 10; }
[ ! -L av15/mcpv5_secure_http_readback.py ] || { echo "BLOCKED=GATE_SYMLINK"; exit 11; }
[ -f av15/mcpv5_secure_http_readback.py ] || { echo "BLOCKED=EXISTING_GATE_MISSING"; exit 12; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for file in av15/kebunate_flow_ui.py av15/kebunate_flow.html av15/test_kebunate_flow_ui.py; do
  git show "FETCH_HEAD:$file" > "$TMP/$(basename "$file")" || exit 13
  if [ -L "$file" ] || { [ -e "$file" ] && [ ! -f "$file" ]; }; then
    echo "BLOCKED=UNSAFE_UI_PATH:$file"; exit 14
  fi
  if [ -f "$file" ] && ! cmp -s "$file" "$TMP/$(basename "$file")"; then
    if ! git cat-file -e "HEAD:$file" 2>/dev/null || ! cmp -s "$file" <(git show "HEAD:$file"); then
      echo "BLOCKED=LOCAL_UI_SOURCE_MODIFIED:$file"; exit 15
    fi
  fi
done

# Existing local gate can differ from GitHub: patch only known, exact auth-gated anchors.
python3 - "$TMP" <<'PY'
import ast, pathlib, sys
original=pathlib.Path("av15/mcpv5_secure_http_readback.py")
s=original.read_text(encoding="utf-8")
start=s.index("class AccessGate:")
end=s.index("\ndef main():",start)
prefix, gate, suffix=s[:start],s[start:end],s[end:]
old='if path != "/mcp":'
new='if path not in ("/mcp", "/flow", "/flow/api"):'
old_return='        return await self.app(scope, receive, send)'
new_return='''        if path.startswith("/flow"):
            from av15.kebunate_flow_ui import handle
            return await handle(scope, receive, send)
        return await self.app(scope, receive, send)'''
if new not in gate:
    if gate.count(old)!=1:
        raise SystemExit("BLOCKED=UNKNOWN_SECURITY_GATE_ROUTE_ANCHOR")
    gate=gate.replace(old,new,1)
if new_return not in gate:
    if gate.count(old_return)!=1:
        raise SystemExit("BLOCKED=UNKNOWN_SECURITY_GATE_DISPATCH_ANCHOR")
    gate=gate.replace(old_return,new_return,1)
candidate=prefix+gate+suffix
ast.parse(candidate)
(pathlib.Path(sys.argv[1])/"gate.py").write_text(candidate,encoding="utf-8")
print("GATE_TARGETED_PATCH=SYNTAX_PASS")
PY
# This checkpoint is never overwritten. Preserve unknown local edits.
BACKUP="$HOME/KnowingMind/checkpoints/flow-security-gate-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
cp -p av15/mcpv5_secure_http_readback.py "$BACKUP/mcpv5_secure_http_readback.py"
echo "ORIGINAL_SOURCE_SAVED=$BACKUP"
install -m 644 "$TMP/gate.py" av15/mcpv5_secure_http_readback.py
for file in av15/kebunate_flow_ui.py av15/kebunate_flow.html av15/test_kebunate_flow_ui.py; do
  install -m 644 "$TMP/$(basename "$file")" "$file"
  echo "SOURCE_INSTALLED=$file"
done

echo "=== SECURITY TESTS ==="
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  av15/test_kebunate_flow_ui.py av15/test_mcpv5_secure_http_readback.py
echo "SECURITY_QC=PASS"

# Only give read permissions; no execution rights/registry writes.
echo "=== EXISTING DB READBACK PERMISSION ==="
sudo -v
sudo -u postgres psql -X -w -v ON_ERROR_STOP=1 -d km_microops <<'SQL'
BEGIN;
SELECT current_database() AS current_db;
GRANT SELECT ON circuit.registry,circuit.bindings,circuit.relations,
 circuit.routes,circuit.usage_events,circuit.core_memory TO siango;
COMMIT;
SQL
KMCO_PG_SERVICE=kmco_runtime PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from av15.kebunate_flow_ui import _pg_snapshot
s=_pg_snapshot()
print("DB_STATE="+s["state"])
if s["state"]!="DB_READBACK":
    raise SystemExit("BLOCKED=FLOW_DB_READBACK:"+s.get("reason","UNKNOWN"))
print("USAGE_EVENTS="+str(s["tables"]["usage_events"]))
print("CORE_CASES="+str(s["tables"]["core_memory"]))
PY

echo "=== EXISTING ACCESS IDENTITY ==="
# Do not print any credential. Do not accept unverifiable manually copied tokens.
SUBJECT="$(python3 - <<'PY'
import subprocess,jwt
from av15.mcpv5_secure_http_readback import ISSUER,AUDIENCE
try:
 p=subprocess.run(["cloudflared","access","token","-app","https://mcp.knowingmind.app"],
                  capture_output=True,text=True,timeout=15)
 if p.returncode: raise ValueError("CLOUDFLARE_ACCESS_LOGIN_REQUIRED")
 raw=p.stdout.strip()
 key=jwt.PyJWKClient(ISSUER+"/cdn-cgi/access/certs",timeout=5).get_signing_key_from_jwt(raw)
 claims=jwt.decode(raw,key.key,algorithms=["RS256"],audience=AUDIENCE,
                   issuer=ISSUER,options={"require":["sub","exp","iat","iss","aud"]})
 if claims.get("type")!="app" or not claims.get("sub"):
    raise ValueError("EXPECTED_ACCESS_APP_TOKEN")
 print(claims["sub"])
except Exception:
 raise SystemExit(2)
PY
)" || { echo "BLOCKED=SIGNED_ACCESS_SUBJECT_UNAVAILABLE"; exit 22; }
case "$SUBJECT" in ""|*[!a-zA-Z0-9._:@-]*) echo "BLOCKED=SUBJECT_FORMAT"; exit 23;; esac
echo "SIGNED_ACCESS_IDENTITY=RESOLVED_NOT_PRINTED"

# No second origin, and no changes to existing service units.
if (echo >/dev/tcp/127.0.0.1/18856) >/dev/null 2>&1; then
  echo "BLOCKED=EXISTING_ORIGIN_ALREADY_LISTENING_REQUIRES_IN_PLACE_RESTART"
  exit 24
fi
UNIT=/etc/systemd/system/kebunate-mcpv5-readback.service
ENV_FILE=/etc/kebunate/mcpv5-flow.env
if sudo test -e "$UNIT"; then
  echo "BLOCKED=SYSTEMD_ORIGIN_UNIT_ALREADY_EXISTS_PRESERVED"
  exit 25
fi
if sudo test -e "$ENV_FILE"; then
  echo "BLOCKED=ORIGIN_ENV_ALREADY_EXISTS_PRESERVED"
  exit 26
fi
PYTHON="$(command -v python3)"
WORKDIR="$PWD"
sudo install -d -m 700 /etc/kebunate
printf 'KEBUNATE_HTTP_READBACK_CONFIRM=YES\nKEBUNATE_ACCESS_SUB=%s\nKMCO_PG_SERVICE=kmco_runtime\n' "$SUBJECT" |
  sudo install -m 600 /dev/stdin "$ENV_FILE"
cat > "$TMP/origin.service" <<EOF
[Unit]
Description=KEBUNATE existing MCP V5 origin plus Flow Studio
After=network-online.target postgresql.service
Wants=network-online.target
[Service]
Type=simple
User=siango
WorkingDirectory=$WORKDIR
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=$ENV_FILE
ExecStart=$PYTHON -m av15.mcpv5_secure_http_readback
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
EOF
sudo install -m 644 "$TMP/origin.service" "$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now kebunate-mcpv5-readback.service
sleep 2
if ! sudo systemctl is-active --quiet kebunate-mcpv5-readback.service; then
  echo "BLOCKED=ORIGIN_SERVICE_NOT_ACTIVE"
  exit 27
fi
CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:18856/flow)" || true
[ "$CODE" = 401 ] || { echo "BLOCKED=ANONYMOUS_FLOW_DENIAL_FAILED:$CODE"; exit 28; }
echo "FLOW_ANONYMOUS_HTTP=401"
# Verify signed JWT route (do not print raw JWT or claims).
KMCO_PG_SERVICE=kmco_runtime PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import subprocess,urllib.request,json
p=subprocess.run(["cloudflared","access","token","-app","https://mcp.knowingmind.app"],capture_output=True,text=True,timeout=15,check=True)
token=p.stdout.strip()
for path,typ in [("/flow","text/html"),("/flow/api","application/json")]:
 req=urllib.request.Request("http://127.0.0.1:18856"+path,headers={"Cf-Access-Jwt-Assertion":token})
 with urllib.request.urlopen(req,timeout=8) as response:
  data=response.read(200000)
  if response.status!=200 or typ not in response.headers.get("Content-Type",""):
   raise SystemExit("BLOCKED=LOCAL_AUTHENTICATED_FLOW_RESPONSE")
  if path.endswith("/api"):
   parsed=json.loads(data)
   if parsed.get("db",{}).get("state")!="DB_READBACK":
    raise SystemExit("BLOCKED=FLOW_DB_LIVE_READBACK")
 print("SIGNED_LOCAL_FLOW="+path+" PASS")
PY
echo "AV15_FLOW_ORIGIN=LOCAL_AUTHENTICATED_PASS"
echo "ADMIN_PUBLIC_ROUTE=NOT_DEPLOYED_OR_TESTED"
echo "REMOTE_CLOUDFLARE_BROWSER=NOT_TESTED"
echo "GITHUB_RUNNER=NOT_REQUIRED_FOR_LOCAL_FLOW"
echo "RESULT=FLOW_LOCAL_LIVE_READY_REMOTE_DEPLOY_PENDING"
