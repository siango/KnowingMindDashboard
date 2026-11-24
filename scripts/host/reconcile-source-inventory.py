#!/usr/bin/env python3
"""Read-only multi-host Git source inventory for KEBUNATE reconciliation.

Inspect explicitly supplied workspace roots. Never fetch, checkout, reset,
stage, push, copy, read credentials, run untrusted hooks or modify repositories.
Outputs metadata only; local records are NOT authoritative production receipts.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GH_RE = re.compile(r"(?:github\.com[:/])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$")
MAX_REPOS = 120
MAX_SCAN = 1200

def cmd(path: Path, *args: str):
    try:
        p = subprocess.run(["git", "-C", str(path), *args], text=True,
                           capture_output=True, timeout=6, check=False,
                           env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_OPTIONAL_LOCKS": "0",
                                "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None

def candidates(roots):
    examined = 0
    found = set()
    for root in roots:
        root = root.expanduser().resolve()
        if not root.is_dir():
            continue
        stack = [(root, 0)]
        while stack and examined < MAX_SCAN and len(found) < MAX_REPOS:
            path, depth = stack.pop()
            examined += 1
            if (path / ".git").exists():
                found.add(path)
                continue
            if depth >= 3:
                continue
            try:
                children = sorted([p for p in path.iterdir() if p.is_dir() and not p.is_symlink()],
                                  key=lambda p: p.name)
            except OSError:
                continue
            for child in reversed(children):
                if child.name not in {".cache", ".venv", "node_modules", ".local",
                                      ".npm", "state", "backups", ".git"}:
                    stack.append((child, depth + 1))
    return sorted(found), examined >= MAX_SCAN or len(found) >= MAX_REPOS

def inspect(path: Path):
    sha = cmd(path, "rev-parse", "HEAD")
    branch = cmd(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote = cmd(path, "remote", "get-url", "origin")
    match = GH_RE.search(remote or "")
    repo = f"{match.group(1)}/{match.group(2)}" if match else "UNKNOWN_OR_NON_GITHUB"
    tracked = cmd(path, "status", "--porcelain=v1", "-uno")
    untracked = cmd(path, "ls-files", "--others", "--exclude-standard")
    return {
        "workspace": str(path),
        "github_repository": repo,
        "head_sha": sha if sha and SHA_RE.fullmatch(sha) else None,
        "branch": branch or "DETACHED_OR_UNKNOWN",
        "tracked_changes": None if tracked is None else len(tracked.splitlines()) if tracked else 0,
        "untracked_files": None if untracked is None else len(untracked.splitlines()) if untracked else 0,
        "uncommitted_data_preservation_required": (
            tracked is None or untracked is None or bool(tracked) or bool(untracked)),
        "source_comparison": "PENDING_CROSS_HOST_RECONCILIATION",
        "backup_verified": False,
    }

def inventory(roots, node):
    repos, truncated = candidates(roots)
    rows = [inspect(p) for p in repos]
    return {
        "schema": "KEBUNATE_MULTI_HOST_SOURCE_INVENTORY_V1",
        "mission": "G0-G1",
        "node": node,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "PARTIAL_SCAN" if truncated else "LOCAL_INVENTORY_ONLY",
        "repositories": rows,
        "repository_count": len(rows),
        "requires_preservation": sum(bool(r["uncommitted_data_preservation_required"]) for r in rows),
        "other_host_coverage": "NOT_INSPECTED",
        "postgresql_backup": "NOT_VERIFIED",
        "runtime_acceptance": "NOT_VERIFIED",
        "production_cutover": "NOT_GRANTED",
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node", required=True, choices=["ubuntu-usb", "ubuntu-ssd", "wsl"])
    ap.add_argument("--root", type=Path, action="append", required=True,
                    help="Existing workspace root; repeat for additional roots")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    result = inventory(args.root, args.node)
    wire = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.is_symlink():
            ap.error("SYMLINK_OUTPUT_DENIED")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(wire, encoding="utf-8")
    print(wire, end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
