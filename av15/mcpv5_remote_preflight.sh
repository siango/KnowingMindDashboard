#!/usr/bin/env bash
# KEBUNATE MCP V5 secure route preflight — READ ONLY, no secrets, no deployment.
# Exit 0 means evidence inventory completed, NOT production authorization.
set -u
ROOT="${HOME}/KnowingMind/workspaces/kmco-github-qc"
OUT="${ROOT}/av15/state/mcpv5-remote-preflight.json"
mkdir -p "${ROOT}/av15/state" || exit 2
python3 - "$ROOT" "$OUT" <<'PY'
import json, os, pathlib, shutil, subprocess, sys, datetime, urllib.request, urllib.error
root=pathlib.Path(sys.argv[1])
out=pathlib.Path(sys.argv[2])
def run(cmd,timeout=8):
 try:
  r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
  return {"exit":r.returncode,"stdout":r.stdout[:2000],"stderr_class":("ERROR" if r.returncode else "NONE")}
 except (OSError,subprocess.TimeoutExpired):
  return {"exit":None,"stdout":"","stderr_class":"UNAVAILABLE"}
def yes(s): return bool(s)
evidence=root/"av15/state/kmco-mcpv5-local-acceptance.json"
result={"schema":"KEBUNATE_MCPV5_REMOTE_PREFLIGHT_V1",
 "timestamp_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
 "mission":"G0-G1-MINIMUM-SELLABLE-DEMO",
 "owner_node":"AV15_USB","mode":"READ_ONLY",
 "local_acceptance_file":evidence.is_file(),
 "local_mcp_source":(root/"av15/coding_worker_v2/agent/mcp_server.py").is_file(),
 "cloudflared_binary":bool(shutil.which("cloudflared")),
 "cloudflared_process_running":False,
 "tunnel_config_files":[],
 "remote_http_status":None,
 "remote_mcp_authenticated":False,
 "remote_tools_callable_verified":False,
 "chatgpt_connector_installed":False,
 "production_control_open":False}
try:
 proc=subprocess.run(["pgrep","-af","cloudflared"],capture_output=True,text=True,timeout=4)
 result["cloudflared_process_running"]=proc.returncode==0 and any("tunnel" in s for s in proc.stdout.splitlines())
except Exception: pass
for base in (pathlib.Path.home()/".cloudflared",pathlib.Path("/etc/cloudflared")):
 for name in ("config.yml","config.yaml"):
  path=base/name
  try:
   if path.is_file():
    result["tunnel_config_files"].append(str(path))
  except (PermissionError, OSError):
   result.setdefault("tunnel_config_inaccessible", []).append(str(path))
# Status code only; do not send tokens, cookies, credentials, POST or tool calls.
try:
 req=urllib.request.Request("https://mcp.knowingmind.app/",method="HEAD",headers={"User-Agent":"kebunate-read-only-route-preflight/1"})
 with urllib.request.urlopen(req,timeout=8) as response:
  result["remote_http_status"]=response.status
except urllib.error.HTTPError as exc:
 result["remote_http_status"]=exc.code
except Exception:
 result["remote_http_status"]="UNREACHABLE"
out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
print(json.dumps(result,indent=2,sort_keys=True))
print("PREFLIGHT_EVIDENCE:",out)
print("WARNING: HTTP reachability is not authenticated MCP acceptance.")
PY
