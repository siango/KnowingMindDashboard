"""Internal, reversible project-local installation; no downloaded dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

from .agent.memory import current_base_commit
from .agent.pilot import SCHEMA, TOOLS, load_context, validate_config

SOURCE = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "pilot" / "kmcw.agent.md"


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files():
    worker = SOURCE / "coding_worker_v2"
    return [SOURCE / "km", *sorted(worker.glob("*.py")),
            *sorted((worker / "agent").glob("*.py")), TEMPLATE,
            worker / "pilot" / "config.example.json", worker / "pilot" / "README.md"]


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".kmcw-tmp")
    with temp.open("x", encoding="utf-8") as output:
        output.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())
    temp.replace(path)


def _read(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


def install(args):
    root = Path(args.workspace).resolve()
    target = root / ".kmcw"
    manifest_path = target / "install.json"
    agent_path = root / ".github" / "agents" / "kmcw.agent.md"
    mcp_path = root / ".mcp.json"
    github_mcp = root / ".github" / "mcp.json"
    if (target.exists() or agent_path.exists() or agent_path.is_symlink()
            or mcp_path.is_symlink() or any(
                (root / part).is_symlink() for part in (".github", ".github/agents"))):
        raise ValueError("installation paths already exist or are symlinked; refusing overwrite")
    if not mcp_path.exists() and github_mcp.exists():
        raise ValueError(".github/mcp.json exists; refusing to shadow it with .mcp.json")
    mcp = _read(mcp_path) if mcp_path.exists() else {"mcpServers": {}}
    if not isinstance(mcp.get("mcpServers"), dict) or "kmcw" in mcp["mcpServers"]:
        raise ValueError("MCP configuration invalid or kmcw already registered")
    data = {
        "schema": SCHEMA, "account_id": args.account_id, "worker_id": args.worker_id,
        "workspace": str(root), "runtime_base": str(Path(args.runtime_base).resolve()),
        "allowed_paths": args.allowed_path, "context_paths": args.context_path or args.allowed_path,
        "test_command": args.test_command, "provider": args.provider,
        "authorize_subscription": args.authorize_subscription,
        "memory_ranking": args.memory_ranking,
        "auditor_root": args.auditor_root, "authority_snapshot": args.authority_snapshot,
        "verifier_public_key": args.verifier_public_key,
        "source_sha": (_read(SOURCE / "DISTRIBUTION.json")["source_sha"]
                       if (SOURCE / "DISTRIBUTION.json").is_file()
                       else current_base_commit(str(SOURCE))),
        "source_root": str(SOURCE),
        "source_files": {str(p.relative_to(SOURCE)): _hash(p) for p in source_files()},
    }
    validate_config(data)
    config_path = target / "config.json"
    server = {"type": "local", "command": str(SOURCE / "km"),
              "args": ["pilot", "mcp", "--config", str(config_path)],
              "cwd": str(SOURCE), "tools": list(TOOLS),
              "env": {"KMW2_SOURCE_ROOT": "", "KMW2_SOURCE_SHA": "",
                      "KMW2_OWNER_NODE": args.worker_id}}
    target.mkdir(mode=0o700)
    manifest = {"schema": SCHEMA, "workspace": str(root),
                "server": server, "files": {}, "source_sha": data["source_sha"],
                "distribution": "INTERNAL_ONLY_LICENSE_UNCONFIRMED"}
    _write(config_path, data)
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    with agent_path.open("x", encoding="utf-8") as output:
        output.write(TEMPLATE.read_text(encoding="utf-8"))
    manifest["files"] = {str(p.relative_to(root)): _hash(p) for p in (config_path, agent_path)}
    # Write recovery metadata before changing shared MCP configuration.
    _write(manifest_path, manifest)
    mcp["mcpServers"]["kmcw"] = server
    _write(mcp_path, mcp)
    return {"installed": True, "config": str(config_path),
            "source_sha": data["source_sha"], "distribution": manifest["distribution"]}


def uninstall(args):
    root = Path(args.workspace).resolve()
    target = root / ".kmcw"
    manifest = _read(target / "install.json")
    if manifest.get("schema") != SCHEMA or manifest.get("workspace") != str(root):
        raise ValueError("installation manifest mismatch")
    expected = {".kmcw/config.json", ".github/agents/kmcw.agent.md"}
    if set(manifest.get("files", {})) != expected:
        raise ValueError("installation file scope mismatch")
    mcp_path = root / ".mcp.json"
    if target.is_symlink() or mcp_path.is_symlink():
        raise ValueError("installation paths changed")
    mcp = _read(mcp_path)
    if mcp.get("mcpServers", {}).get("kmcw") != manifest["server"]:
        raise ValueError("kmcw MCP entry changed; refusing to remove it")
    for rel, digest in manifest["files"].items():
        path = root / rel
        if (path.is_symlink() or not path.is_file() or _hash(path) != digest
                or not path.resolve().is_relative_to(root)):
            raise ValueError("installed files changed; preserve and resolve before uninstall")
    del mcp["mcpServers"]["kmcw"]
    _write(mcp_path, mcp)
    for rel in manifest["files"]:
        (root / rel).unlink()
    (target / "install.json").unlink()
    if not list(target.iterdir()):
        target.rmdir()
    return {"uninstalled": True, "runtime_and_receipts": "PRESERVED",
            "mcp_other_entries": "PRESERVED"}


def package(args):
    destination = Path(args.output).resolve()
    if destination.exists():
        raise ValueError("package output exists")
    files = source_files()
    # Explicit file allowlist excludes env, runtime state, keys and credentials.
    metadata = {
        "schema": "KMCW_INTERNAL_DISTRIBUTION_V1",
        "source_sha": current_base_commit(str(SOURCE)),
        "distribution": "BLOCKED_EXTERNAL_SOURCE_LICENSE_UNCONFIRMED",
        "dependencies": {"python": ">=3.12 stdlib only", "bash": "launcher; not bundled",
                         "git": "worktrees; not bundled", "openssl": "signature readback; not bundled",
                         "copilot": "customer-installed/authenticated; not bundled",
                         "ollama": "optional customer-installed provider; not bundled"},
        "files": {str(p.relative_to(SOURCE)): _hash(p) for p in files},
        "secrets": "NO_ENV_KEYS_CREDENTIALS_OR_STATE_INCLUDED",
    }
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, str(path.relative_to(SOURCE)))
        archive.writestr("DISTRIBUTION.json", json.dumps(metadata, indent=2) + "\n")
    return {"package": str(destination), "sha256": _hash(destination), **metadata}


def add_parser(sub):
    parser = sub.add_parser("pilot", help="internal project install / MCP / readback")
    actions = parser.add_subparsers(dest="pilot_action", required=True)
    setup = actions.add_parser("install")
    setup.add_argument("--workspace", required=True)
    setup.add_argument("--runtime-base", required=True)
    setup.add_argument("--account-id", required=True)
    setup.add_argument("--worker-id", default="AV15")
    setup.add_argument("--allowed-path", action="append", required=True)
    setup.add_argument("--context-path", action="append")
    setup.add_argument("--test-command", default="python3 -m unittest")
    setup.add_argument("--provider", choices=("qwen-local", "copilot-cli"), default="qwen-local")
    setup.add_argument("--authorize-subscription", action="store_true")
    setup.add_argument("--memory-ranking", choices=("baseline", "qc"), default="baseline")
    setup.add_argument("--auditor-root", default="")
    setup.add_argument("--authority-snapshot", default="")
    setup.add_argument("--verifier-public-key", default="")
    remove = actions.add_parser("uninstall")
    remove.add_argument("--workspace", required=True)
    pack = actions.add_parser("package")
    pack.add_argument("--output", required=True)
    mcp = actions.add_parser("mcp")
    mcp.add_argument("--config", required=True)
    call = actions.add_parser("call")
    call.add_argument("tool", choices=TOOLS)
    call.add_argument("--config", required=True)
    call.add_argument("--args", default="{}")
    bench = actions.add_parser("benchmark")
    bench.add_argument("--workspace", required=True)
    bench.add_argument("--runtime-base", required=True)
    bench.add_argument("--account-id", default="")
    bench.add_argument("--cases", required=True)
    bench.add_argument("--rounds", type=int, default=10)
    bench.add_argument("--corpus-kind", choices=("synthetic-fixture", "historical-observation"),
                       required=True)
    orchestration = actions.add_parser("orchestration-usage",
                                       help="classify a Copilot session usage file; read-only")
    orchestration.add_argument("--file", required=True)
    orchestration.add_argument("--plan-id", required=True)


def main(args):
    if args.pilot_action == "orchestration-usage":
        from .agent.pilot import orchestration_usage
        result = orchestration_usage(args.file, args.plan_id)
    elif args.pilot_action == "benchmark":
        from .pilot_benchmark import run
        result = run(args)
    elif args.pilot_action in ("install", "uninstall", "package"):
        result = {"install": install, "uninstall": uninstall, "package": package}[args.pilot_action](args)
    else:
        ctx = load_context(args.config)
        from .agent.mcp_server import _call, serve
        if args.pilot_action == "mcp":
            return serve(ctx)
        from .agent.tools import build_default_registry
        result = _call(build_default_registry(),
                       {"name": args.tool, "arguments": json.loads(args.args)}, ctx)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
