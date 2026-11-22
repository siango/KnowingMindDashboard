#!/usr/bin/env python3
"""Install a pinned KMCP wheel and Wrangler on the existing Ubuntu node.
No root, service restart, credential creation, production queue or authority change.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib import request, error

def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, timeout=kwargs.pop("timeout", 180), **kwargs)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--source-sha", required=True)
    ap.add_argument("--receipt", required=True)
    args = ap.parse_args()
    assert os.environ.get("RUNNER_NAME") == "ubuntu-usb", "Wrong runner"
    assert os.geteuid() != 0, "Ordinary user required"
    bundle = Path(args.bundle).resolve()
    manifest = json.loads((bundle / "release.json").read_text())
    assert manifest["source_sha"] == args.source_sha, "Source SHA mismatch"
    for rel, expected in manifest["files"].items():
        path = (bundle / rel).resolve()
        assert path.is_relative_to(bundle) and path.is_file(), "Invalid artifact path"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, "Artifact digest mismatch"
    home = Path.home()
    assert shutil.disk_usage(home).free > 1024**3, "At least 1 GiB free is required"
    receipt = {
        "schema": "KMCP_UBUNTU_INSTALLATION_V1",
        "mission_id": "KMCP-V5-PRODUCTION",
        "node_id": "ubuntu-usb",
        "source_sha": args.source_sha,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "service_changes": False,
        "production_authority_changed": False,
        "cloudflare_deployed": False,
        "reboot_persistence": "NOT_VERIFIED",
        "steps": {}
    }
    try:
        wheels = list(bundle.glob("*.whl"))
        assert len(wheels) == 1, "Expected exactly one wheel"
        venv = home / "KnowingMind/.venvs/kmcp-portable"
        marker = venv / ".knowingmind-managed.json"
        if venv.exists() and not marker.exists():
            raise RuntimeError("Existing unowned venv: inspect before replacement")
        venv.parent.mkdir(parents=True, exist_ok=True)
        if not venv.exists():
            run([sys.executable, "-m", "venv", venv])
            marker.write_text(json.dumps({"schema": "KMCP_MANAGED_VENV_V1", "state": "INSTALLING"}))
        previous = json.loads(marker.read_text())
        py = venv / "bin/python"
        if previous.get("source_sha") != args.source_sha:
            run([py, "-m", "pip", "install", "--no-index", "--no-deps",
                 "--force-reinstall", "--no-cache-dir", wheels[0]])
        run([venv / "bin/kmcp", "--help"])
        query = "import json,coding_worker_v2; from coding_worker_v2.agent.tools import build_default_registry; r=build_default_registry(circuits=True); print(json.dumps({'module':coding_worker_v2.__file__,'tool_count':len(r.names()),'circuit_tools':[x for x in r.names() if x.startswith('kmcp.')]}))"
        installed = json.loads(run([py, "-I", "-c", query], capture_output=True, text=True).stdout)
        assert Path(installed["module"]).is_relative_to(venv), "Import did not use installed package"
        assert installed["tool_count"] == 73 and len(installed["circuit_tools"]) == 6
        receipt["steps"]["package"] = {"status": "INSTALLED_VERIFIED", "venv": str(venv), **installed}
        links = home / ".local/bin"
        links.mkdir(parents=True, exist_ok=True)
        link = links / "kmcp"
        target = venv / "bin/kmcp"
        if link.exists() or link.is_symlink():
            assert link.is_symlink() and link.resolve() == target.resolve(), "Existing kmcp command is not installer-owned"
        else:
            link.symlink_to(target)
        marker.write_text(json.dumps({"schema": "KMCP_MANAGED_VENV_V1", "state": "INSTALLED",
                                     "source_sha": args.source_sha, "wheel_sha256": manifest["files"][wheels[0].name]}, indent=2))
        # Protocol smoke uses a clearly isolated fixture, never production task state.
        with tempfile.TemporaryDirectory(prefix="kmcp-install-smoke-") as tmp:
            root = Path(tmp)
            workspace = root / "fixture"
            workspace.mkdir()
            run(["git", "init", "-q", workspace])
            config = root / "runtime.json"
            config.write_text(json.dumps({
                "schema": "KMCP_RUNTIME_V1", "node_id": "ubuntu-usb",
                "account_id": "INSTALLATION_SMOKE_FIXTURE",
                "mission_id": "KMCP-V5-PRODUCTION",
                "task_id": "INSTALLATION_SMOKE_FIXTURE",
                "workspace": str(workspace), "runtime_base": str(root / "fixture-state")
            }))
            payloads = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                            "clientInfo": {"name": "kmcp-install-smoke", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            ]
            proc = run([venv / "bin/kmcp", "--config", config, "serve"],
                       input="".join(json.dumps(p)+"\n" for p in payloads),
                       capture_output=True, text=True)
            replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
            listed = next(x for x in replies if x.get("id") == 2)
            assert len(listed["result"]["tools"]) == 73
            receipt["steps"]["stdio_protocol"] = {"status": "PASS", "tools": 73,
                                                  "scope": "ISOLATED_INSTALLATION_FIXTURE"}
        # Fast-forward the already existing setup checkout; never reset local edits.
        checkout = home / "kmcp-host-setup"
        assert (checkout / ".git").exists(), "Existing setup checkout not found"
        dirty = run(["git", "-C", checkout, "status", "--porcelain", "--untracked-files=no"],
                    capture_output=True, text=True).stdout
        assert not dirty.strip(), "Setup checkout has local tracked changes"
        branch = run(["git", "-C", checkout, "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
        assert branch == "feature/kmcp-portable-circuits-20261122", "Unexpected setup branch"
        run(["git", "-C", checkout, "fetch", "--quiet", "origin", args.source_sha])
        run(["git", "-C", checkout, "merge", "--ff-only", "--quiet", "FETCH_HEAD"])
        got = run(["git", "-C", checkout, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        assert got == args.source_sha
        receipt["steps"]["existing_checkout"] = {"status": "FAST_FORWARDED", "path": str(checkout)}
        env = dict(os.environ, PATH=str(home / ".local/bin")+os.pathsep+os.environ["PATH"],
                   WRANGLER_SEND_METRICS="false", CI="true")
        node_major = int(run(["node", "-p", "process.versions.node.split('.')[0]"], env=env,
                             capture_output=True, text=True).stdout.strip())
        assert node_major >= 22, "Node >=22 required"
        project = checkout / "cloudflare/kmcp-edge"
        run(["npm", "ci", "--no-audit", "--no-fund"], cwd=project, env=env, timeout=480)
        run(["npm", "test"], cwd=project, env=env)
        run(["npm", "run", "check"], cwd=project, env=env)
        wrangler = project / "node_modules/.bin/wrangler"
        version = run([wrangler, "--version"], cwd=project, env=env,
                      capture_output=True, text=True).stdout.strip()
        receipt["steps"]["cloudflare_tools"] = {"status": "INSTALLED_BUNDLE_VERIFIED", "version": version,
                                              "project": str(project), "deployed": False}
        # Inspect only authentication outcome. Never return tokens, cookies or raw auth output.
        auth = subprocess.run([str(wrangler), "whoami"], cwd=project, env=env,
                              capture_output=True, text=True, timeout=45)
        auth_text = (auth.stdout + auth.stderr).lower()
        anonymous = any(x in auth_text for x in ("not authenticated", "not logged in", "not currently logged"))
        receipt["steps"]["cloudflare_auth"] = {
            "status": "AVAILABLE" if auth.returncode == 0 and not anonymous else "LOGIN_REQUIRED_OR_UNAVAILABLE",
            "returncode": auth.returncode}
        # Existing HTTP authentication gate must remain closed to anonymous requests.
        try:
            with request.urlopen("http://127.0.0.1:18856/mcp", timeout=8) as response:
                status = response.status
        except error.HTTPError as exc:
            status = exc.code
        except OSError:
            status = None
        receipt["steps"]["existing_http"] = {
            "anonymous_status": status,
            "status": "AUTH_GATE_OBSERVED" if status in (401, 403) else "REVIEW_REQUIRED",
            "authenticated_acceptance": "NOT_PERFORMED"}
        receipt["status"] = "PACKAGE_AND_CLOUDFLARE_TOOLS_INSTALLED"
    except Exception as exc:
        receipt["status"] = "PARTIAL_INSTALLATION"
        # Bounded class/message for our assertions, no subprocess stdout/stderr with credentials.
        receipt["blocker"] = {"type": type(exc).__name__,
                              "reason": str(exc)[:240] if isinstance(exc, (AssertionError, RuntimeError)) else type(exc).__name__}
        raise
    finally:
        receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
        path = Path(args.receipt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2)+"\n")
        persistent = home / "KnowingMind/state/installations/kmcp-portable.json"
        persistent.parent.mkdir(parents=True, exist_ok=True)
        persistent.write_text(path.read_text())
        print(json.dumps(receipt, indent=2))

if __name__ == "__main__":
    main()
