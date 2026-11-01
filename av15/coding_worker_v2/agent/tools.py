"""V3 Phase 3 / V3.1 Phase 5: tool router over an allowlisted registry.

There is no executable in-repo MCP server to adopt (vendored hub holds
contract doctrine only; Dhamma MCP is harness-side), so the agent
discovers local tools dynamically from availability probes and routes
exclusively through this registry. Unknown tools are rejected; tool
output is data and is never executed as shell (list-form subprocess
only, no shell=True anywhere in this module).
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..pipeline import inspect_repo, run_tests
from ..providers import http_json, redact
from ..receipts import read_receipt
from ..tasks import Task

PERMISSIONS = {"read", "write", "destructive"}
TYPE_MAP = {"str": str, "int": int, "bool": bool, "list": list, "dict": dict}


class ToolError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {redact(detail)[:300]}" if detail else reason)
        self.reason = reason
        self.detail = redact(detail)[:2000]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    capability: str
    required: dict = field(default_factory=dict)  # arg -> type name
    optional: dict = field(default_factory=dict)
    permission: str = "read"
    side_effects: str = "none"
    limits: str = ""


def _run(argv: list[str], cwd: str, timeout: int = 60) -> tuple[int, str]:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=False)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))[-8000:]


def _check_type(value, type_name: str) -> bool:
    expected = TYPE_MAP.get(type_name)
    if expected is None:
        return False
    if type_name == "int" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


class ToolRegistry:
    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}
        self._fns: dict[str, object] = {}
        self._health: dict[str, object] = {}

    def register(self, spec: ToolSpec, fn, health=None) -> None:
        if spec.permission not in PERMISSIONS:
            raise ToolError("TOOL_SPEC_INVALID", spec.name)
        self._specs[spec.name] = spec
        self._fns[spec.name] = fn
        if health is not None:
            self._health[spec.name] = health

    def names(self) -> list[str]:
        return sorted(self._specs)

    def discover(self, ctx: dict) -> list[dict]:
        """Dynamic capability discovery. Never raises."""
        out = []
        for name in self.names():
            spec = self._specs[name]
            available, reason = True, "OK"
            probe = self._health.get(name)
            if probe is not None:
                try:
                    available, reason = probe(ctx)
                except Exception as exc:
                    available, reason = False, f"PROBE_CRASH_{type(exc).__name__}"
            out.append({"tool": name, "capability": spec.capability,
                        "permission": spec.permission, "available": available,
                        "reason": redact(reason),
                        "required": dict(spec.required),
                        "optional": dict(spec.optional),
                        "side_effects": spec.side_effects, "limits": spec.limits})
        return out

    def call(self, name: str, args: dict, ctx: dict) -> dict:
        started = time.monotonic()
        try:
            return self._call_inner(name, args, ctx)
        except ToolError as exc:
            raise exc
        except Exception as exc:
            raise ToolError("TOOL_CRASH", f"{name} {type(exc).__name__}") from None
        finally:
            ms = int((time.monotonic() - started) * 1000)
            evidence = ctx.setdefault("evidence", [])
            if isinstance(evidence, list):
                evidence.append({"event": "tool_called", "tool": name,
                                 "ok": f"_{name}_ok" in ctx, "ms": ms})

    def _call_inner(self, name: str, args: dict, ctx: dict) -> dict:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolError("TOOL_UNKNOWN", name[:80])
        if not isinstance(args, dict):
            raise ToolError("TOOL_ARGS_INVALID", name)
        for key in args:
            if key not in spec.required and key not in spec.optional:
                raise ToolError("TOOL_ARG_UNKNOWN", f"{name}.{key}"[:120])
        for key, type_name in spec.required.items():
            if key not in args:
                raise ToolError("TOOL_ARG_MISSING", f"{name}.{key}")
            if not _check_type(args[key], type_name):
                raise ToolError("TOOL_ARG_TYPE", f"{name}.{key} want {type_name}")
        for key, type_name in spec.optional.items():
            if key in args and not _check_type(args[key], type_name):
                raise ToolError("TOOL_ARG_TYPE", f"{name}.{key} want {type_name}")
        if spec.permission == "destructive" and ctx.get("authorize_destructive") is not True:
            raise ToolError("PERMISSION_DENIED", f"{name} needs explicit authorization")
        probe = self._health.get(name)
        if probe is not None:
            available, reason = probe(ctx)
            if not available:
                raise ToolError("TOOL_UNAVAILABLE", f"{name} {reason}")
        result = self._fns[name](args, ctx)
        ctx[f"_{name}_ok"] = True
        if not isinstance(result, dict):
            raise ToolError("TOOL_RESULT_INVALID", name)
        return result


def _need(ctx: dict, key: str):
    value = ctx.get(key)
    if value is None:
        raise ToolError("TOOL_CONTEXT_MISSING", key)
    return value


def _repo(args: dict) -> str:
    repo = args["repo"]
    if not Path(repo).is_dir():
        raise ToolError("TOOL_REPO_MISSING", repo[:120])
    return repo


def search_code(repo: str, pattern: str, max_matches: int = 30) -> list[dict]:
    """Bounded code search. ripgrep when present, else stdlib walk."""
    if not pattern or len(pattern) > 200:
        raise ToolError("TOOL_PATTERN_INVALID")
    out: list[dict] = []
    rg = shutil.which("rg")
    if rg:
        try:
            file_list = subprocess.run(
                [rg, "--files", "-g", "!.git/**"], cwd=repo,
                capture_output=True, text=True, timeout=30, check=False)
            if file_list.returncode not in (0, 1):
                raise ToolError("TOOL_SEARCH_FAIL", "file inventory failed")
            files = sorted((file_list.stdout or "").splitlines())[:200]
            if not files:
                return out
            proc = subprocess.run(
                [rg, "--no-heading", "--with-filename", "--line-number",
                 "--max-count", "5",
                 "-e", pattern, "--", *files],
                cwd=repo, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ToolError("TOOL_SEARCH_FAIL") from None
        for line in (proc.stdout or "").splitlines()[:max_matches]:
            parts = line.split(":", 2)
            if len(parts) == 3:
                out.append({"file": parts[0][:200], "line": parts[1][:12],
                            "text": parts[2].strip()[:200]})
        return out
    root = Path(repo)
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= 2000:
            break
        if not path.is_file() or ".git" in path.parts or path.stat().st_size > 200000:
            continue
        count += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                out.append({"file": str(path.relative_to(root))[:200],
                            "line": str(lineno), "text": line.strip()[:200]})
                if len(out) >= max_matches:
                    return out
    return out


def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()

    def health_repo(ctx):
        return (True, "OK") if ctx.get("workspace") else (False, "WORKSPACE_UNSET")

    def health_gh(ctx):
        _ = ctx
        return (True, "OK") if shutil.which("gh") else (False, "GH_BINARY_ABSENT")

    def health_git(ctx):
        _ = ctx
        return (True, "OK") if shutil.which("git") else (False, "GIT_BINARY_ABSENT")

    reg.register(ToolSpec("repo_inspect", "bounded repository inspection",
                          {"repo": "str"}, {}, "read", "none",
                          "5000 files, 15s git probe"),
                 lambda a, c: inspect_repo(_repo(a)), health_repo)
    reg.register(ToolSpec("code_search", "bounded code search",
                          {"repo": "str", "pattern": "str"}, {"max_matches": "int"},
                          "read", "none", "30 matches, skips .git"),
                 lambda a, c: {"matches": search_code(
                     _repo(a), a["pattern"], min(int(a.get("max_matches", 30)), 100))},
                 health_repo)
    reg.register(ToolSpec("git_diff", "working tree diff (bounded)",
                          {"repo": "str"}, {"max_bytes": "int"}, "read", "none",
                          "default 32k chars"),
                 lambda a, c: _git_diff(a), health_git)
    reg.register(ToolSpec("git_log", "recent commits",
                          {"repo": "str"}, {"n": "int"}, "read", "none",
                          "default 10 commits"),
                 lambda a, c: _git_log(a), health_git)
    reg.register(ToolSpec("queue_health", "queue counts + active leases", {}, {},
                          "read", "none", "local"),
                 lambda a, c: _need(c, "store").health())
    reg.register(ToolSpec("receipt_read", "SSOT local receipt readback",
                          {"task_id": "str"}, {}, "read", "none", "local"),
                 lambda a, c: read_receipt(
                     Path(_need(c, "receipts_dir")) / f"{a['task_id']}.json"))
    reg.register(ToolSpec("ollama_inventory", "live Ollama model inventory", {}, {},
                          "read", "none", "10s probe"),
                 lambda a, c: _ollama_inventory(c))
    reg.register(ToolSpec("test_run", "allowlisted tests, scrubbed env option",
                          {"repo": "str", "command": "str"}, {"timeout_s": "int"},
                          "write", "executes allowlisted tests only",
                          "allowlist + timeout"),
                 lambda a, c: run_tests(_repo(a), a["command"],
                                        min(int(a.get("timeout_s", 300)), 900),
                                        env=c.get("test_env")))
    reg.register(ToolSpec("task_enqueue", "idempotent task enqueue",
                          {"mission_id": "str", "task_id": "str", "repo": "str",
                           "goal": "str"},
                          {"test_command": "str", "max_repairs": "int",
                           "preferred_provider": "str", "owner_node": "str"},
                          "write", "adds queue file", "admission validated"),
                 lambda a, c: _enqueue(a, c))
    reg.register(ToolSpec("git_commit", "commit explicit paths only",
                          {"repo": "str", "message": "str", "paths": "list"},
                          {}, "destructive", "creates commit",
                          "no -A, explicit paths, confirm required"),
                 lambda a, c: _git_commit(a), health_git)
    reg.register(ToolSpec("git_push", "push branch, never force",
                          {"repo": "str", "branch": "str"}, {"remote": "str"},
                          "destructive", "updates remote branch",
                          "no --force, confirm required"),
                 lambda a, c: _git_push(a), health_git)
    reg.register(ToolSpec("pr_create", "open pull request, never merge",
                          {"repo": "str", "title": "str"},
                          {"body": "str", "base": "str"}, "destructive",
                          "opens PR", "no merge, confirm required"),
                 lambda a, c: _pr_create(a), health_gh)
    reg.register(ToolSpec("pr_checks", "PR CI check status", {"repo": "str"},
                          {"pr": "str"}, "read", "none", "60s gh probe"),
                 lambda a, c: _pr_checks(a), health_gh)
    reg.register(ToolSpec("ci_status", "recent workflow runs", {"repo": "str"},
                          {}, "read", "none", "60s gh probe"),
                 lambda a, c: _ci_status(a), health_gh)
    return reg


def _git_diff(args: dict) -> dict:
    repo = _repo(args)
    cap = min(int(args.get("max_bytes", 32000)), 128000)
    rc, out = _run(["git", "-C", repo, "diff", "--stat"], repo)
    rc2, body = _run(["git", "-C", repo, "diff"], repo)
    return {"rc": rc2, "stat": out[:4000], "diff": body[:cap], "truncated": len(body) > cap,
            "clean": (rc == 0 and not out.strip())}

def _git_log(args: dict) -> dict:
    repo = _repo(args)
    rc, out = _run(["git", "-C", repo, "log", "--oneline",
                    f"-{min(int(args.get('n', 10)), 50)}"], repo)
    return {"rc": rc, "commits": out[:4000].splitlines()}


def _enqueue(args: dict, ctx: dict) -> dict:
    store = _need(ctx, "store")
    task = Task.from_dict({
        "mission_id": args["mission_id"], "task_id": args["task_id"],
        "owner_node": args.get("owner_node", "AV15"),
        "input": {"repo": args["repo"], "goal": args["goal"],
                  "test_command": args.get("test_command", ""),
                  "preferred_provider": args.get("preferred_provider", ""),
                  "max_repairs": int(args.get("max_repairs", 2))}})
    task_id, created = store.enqueue(task)
    return {"task_id": task_id, "created": created}


def _git_commit(args: dict) -> dict:
    repo = _repo(args)
    paths = [str(p) for p in args["paths"]]
    if not paths or len(paths) > 50:
        raise ToolError("TOOL_PATHS_INVALID", "need 1..50 explicit paths")
    message = args["message"]
    if not message.strip() or len(message) > 1000:
        raise ToolError("TOOL_MESSAGE_INVALID")
    rc, out = _run(["git", "-C", repo, "add", "--", *paths], repo)
    if rc != 0:
        raise ToolError("TOOL_GIT_FAIL", out)
    rc, out = _run(["git", "-C", repo, "commit", "-m", message], repo)
    if rc != 0:
        raise ToolError("TOOL_GIT_FAIL", out)
    return {"rc": rc, "output": out[:2000]}


def _git_push(args: dict) -> dict:
    repo = _repo(args)
    rc, out = _run(["git", "-C", repo, "push", args.get("remote", "origin"),
                    args["branch"]], repo, timeout=120)
    if rc != 0:
        raise ToolError("TOOL_GIT_FAIL", out)
    return {"rc": rc, "output": out[:2000]}


def _pr_create(args: dict) -> dict:
    repo = _repo(args)
    argv = ["gh", "pr", "create", "--draft", "--title", args["title"]]
    if args.get("body"):
        argv += ["--body", args["body"][:4000]]
    if args.get("base"):
        argv += ["--base", args["base"]]
    rc, out = _run(argv, repo, timeout=120)
    if rc != 0:
        raise ToolError("TOOL_GH_FAIL", out)
    return {"rc": rc, "output": out[:2000]}


def _pr_checks(args: dict) -> dict:
    repo = _repo(args)
    argv = ["gh", "pr", "checks"]
    if args.get("pr"):
        argv.append(args["pr"])
    rc, out = _run(argv, repo)
    return {"rc": rc, "output": out[:4000]}


def _ci_status(args: dict) -> dict:
    repo = _repo(args)
    rc, out = _run(["gh", "run", "list", "--limit", "5"], repo)
    return {"rc": rc, "output": out[:4000]}


def _ollama_inventory(ctx: dict) -> dict:
    from ..providers import require_loopback
    config = _need(ctx, "config")
    try:
        base = require_loopback(config.ollama_url)
        data = http_json(base + "/api/tags", timeout=10)
    except Exception as exc:
        return {"available": False, "reason": getattr(exc, "reason", type(exc).__name__)}
    models = data.get("models", [])
    names = [m.get("name", "?") for m in models if isinstance(m, dict)]
    return {"available": True, "models": names[:50]}
