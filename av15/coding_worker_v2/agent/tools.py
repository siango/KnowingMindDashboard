"""V3 Phase 3 / V3.1 Phase 5: tool router over an allowlisted registry.

There is no executable in-repo MCP server to adopt (vendored hub holds
contract doctrine only; Dhamma MCP is harness-side), so the agent
discovers local tools dynamically from availability probes and routes
exclusively through this registry. Unknown tools are rejected; tool
output is data and is never executed as shell (list-form subprocess
only, no shell=True anywhere in this module).
"""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from ..pipeline import inspect_repo, run_tests
from ..providers import http_json, redact
from ..receipts import read_receipt
from ..tasks import Task, ID_RE
from .sandbox import policy_decision, scrubbed_env

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
        if (not isinstance(spec.name, str) or not spec.name
                or spec.name in self._specs
                or spec.permission not in PERMISSIONS
                or not isinstance(spec.required, dict)
                or not isinstance(spec.optional, dict)
                or set(spec.required) & set(spec.optional)
                or any(not isinstance(key, str) or not key
                       or type_name not in TYPE_MAP
                       for mapping in (spec.required, spec.optional)
                       for key, type_name in mapping.items())
                or not callable(fn)):
            raise ToolError("TOOL_SPEC_INVALID", str(spec.name))
        self._specs[spec.name] = spec
        self._fns[spec.name] = fn
        if health is not None:
            self._health[spec.name] = health

    def names(self) -> list[str]:
        return sorted(self._specs)

    def command_contract(self) -> dict:
        """Deterministic, source-only contract of the existing tool registry.

        Does not execute tools, probe hosts, create authority or report runtime
        readiness. The digest covers the declared commands, not live health.
        """
        commands = [
            {"name":spec.name, "capability":spec.capability,
             "required":dict(spec.required), "optional":dict(spec.optional),
             "permission":spec.permission, "side_effects":spec.side_effects,
             "limits":spec.limits}
            for spec in (self._specs[name] for name in self.names())
        ]
        raw = json.dumps(commands, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
        return {"schema":"KMCW_TOOL_COMMAND_CONTRACT_V1",
                "command_count":len(commands), "commands":commands,
                "sha256":hashlib.sha256(raw).hexdigest()}

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
        success = False
        result_hash, error_class = None, None
        ctx.pop(f"_{name}_ok", None)  # prevent stale success from a prior call
        try:
            result = self._call_inner(name, args, ctx)
            result_hash = hashlib.sha256(json.dumps(
                result, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")).hexdigest()
            success = True
            return result
        except ToolError as exc:
            error_class = exc.reason
            raise exc
        except Exception as exc:
            error_class = "TOOL_CRASH"
            raise ToolError("TOOL_CRASH", f"{name} {type(exc).__name__}") from None
        finally:
            if not success:
                ctx.pop(f"_{name}_ok", None)
            ms = int((time.monotonic() - started) * 1000)
            evidence = ctx.setdefault("evidence", [])
            if isinstance(evidence, list):
                evidence.append({"event": "tool_called", "tool": name,
                                 "ok": success, "ms": ms,
                                 "result_sha256": result_hash,
                                 "error_class": error_class,
                                 "task_id": ctx.get("task_id"),
                                 "mission_id": ctx.get("mission_id")})

    def _call_inner(self, name: str, args: dict, ctx: dict) -> dict:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolError("TOOL_UNKNOWN", name[:80])
        if ctx.get("read_only") and spec.permission != "read":
            raise ToolError("PERMISSION_DENIED", "read-only task")
        if name == "pr_create" and ctx.get("allow_new_pr") is not True:
            raise ToolError("PR_CREATE_NOT_APPROVED",
                            "new pull requests require separate owner approval")
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
        workspace = ctx.get("workspace", "")
        paths = []
        if args.get("repo"):
            try:
                paths = [str(Path(args["repo"]).resolve().relative_to(
                    Path(workspace).resolve()))]
            except ValueError:
                paths = ["../outside-workspace"]
        action = ("test" if name == "test_run" else
                  "checkpoint" if name in {"task_enqueue", "pilot_submit", "pilot_run"} else
                  "inspect" if spec.permission == "read" else name)
        approval = policy_decision(
            action, ctx.get("task_id", "operator"), workspace,
            paths=paths, command=args.get("command", ""), actor="KMCW_TOOL_ROUTER")
        approval["human_authorized"] = (spec.permission == "destructive"
                                         and ctx.get("authorize_destructive") is True)
        ctx.setdefault("evidence", []).append(approval)
        if spec.permission == "destructive" and ctx.get("authorize_destructive") is not True:
            raise ToolError("PERMISSION_DENIED", f"{name} needs explicit authorization")
        if approval["decision"] != "AUTO_APPROVE" and not approval["human_authorized"]:
            raise ToolError("REQUIRE_HUMAN_APPROVAL", approval["rule_id"])
        if name == "receipt_read" and not ID_RE.fullmatch(args["task_id"]):
            raise ToolError("TOOL_ARGS_INVALID", "invalid receipt task id")
        probe = self._health.get(name)
        if probe is not None:
            available, reason = probe(ctx)
            if not available:
                raise ToolError("TOOL_UNAVAILABLE", f"{name} {reason}")
        result = self._fns[name](args, ctx)
        if not isinstance(result, dict):
            raise ToolError("TOOL_RESULT_INVALID", name)
        ctx[f"_{name}_ok"] = True
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


def build_default_registry(photocraft=None) -> ToolRegistry:
    reg = ToolRegistry()
    from .pilot import register_pilot_tools
    register_pilot_tools(reg)

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
                                        env=scrubbed_env(c.get("test_env"))))
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
    if photocraft is not None:
        for local, remote, required, optional, permission in (
            ("list", "session_list", {}, {}, "read"),
            ("inspect", "doc_inspect", {}, {"index": "int"}, "read"),
            ("create", "doc_new", {"name": "str", "width": "int", "height": "int"},
             {"background": "str"}, "write"),
            ("export", "doc_export", {"path": "str"}, {"index": "int"}, "write"),
        ):
            if remote not in photocraft.tools:
                continue
            def invoke(args, ctx, remote=remote):
                return photocraft.call_tool(remote, args)

            def health(ctx, permission=permission):
                if permission != "read":
                    return False, "KCC_GOVERNED_ARTCRAFT_DISPATCH_UNVERIFIED"
                return not photocraft.closed, "MCP_CLOSED" if photocraft.closed else "OK"

            reg.register(ToolSpec(
                f"creative.photocraft.{local}", "creative.photocraft",
                required, optional, permission,
                "none" if permission == "read" else "isolated ArtCraft document/output",
                "discovered stdio tools only; writes pending governed KCC admission"),
                invoke, health)
    for kind in ("health", "task_read", "mission_read", "node_read"):
        field_name = {"task_read": "task_id", "mission_read": "mission_id",
                      "node_read": "node_id"}.get(kind)
        reg.register(ToolSpec(
            f"kcc.d1.{kind}", f"kcc.d1.{kind}",
            {field_name: "str"} if field_name else {}, {}, "read", "none",
            "authenticated existing D1 snapshot; 1 MiB, 10s, freshness 60s; no SQL"),
            lambda a, c, kind=kind: _kcc_read(kind, a, c),
            lambda c: (False, "KCC_LEAST_PRIVILEGE_READ_AUTH_UNVERIFIED"))
    return reg


ARTCRAFT_ROOT = Path.home() / ".local/share/knowingmind/artcraft"
KCC_RUNTIME_ENDPOINT = "https://control-app.kebunate.com/api/control/runtime-v2"
MCP_MAX_BYTES = 1024 * 1024


def _artcraft_path(root: Path, value: str) -> Path:
    path = Path(value)
    if not value or ".." in path.parts or "\\" in value:
        raise ToolError("ARTCRAFT_PATH_DENIED")
    candidate = path if path.is_absolute() else root / path
    if not candidate.is_relative_to(root) or candidate == root:
        raise ToolError("ARTCRAFT_PATH_DENIED")
    for part in (candidate, *candidate.parents):
        if part.is_symlink():
            raise ToolError("ARTCRAFT_SYMLINK_DENIED")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ToolError("ARTCRAFT_PATH_DENIED")
    return candidate


class PhotoCraftStdio:
    """Operator-owned bounded session; not a KCC execution claim or receipt."""

    def __init__(self, timeout_s=10):
        self.timeout_s = timeout_s
        self.closed = False
        self.tools = {}
        self._id = 0
        self._buffer = b""
        binary = Path.home() / ".local/bin/photocraft-cli"
        for root in (ARTCRAFT_ROOT / "input", ARTCRAFT_ROOT / "output"):
            _artcraft_path(ARTCRAFT_ROOT, str(root))
            if not root.is_dir():
                raise ToolError("ARTCRAFT_ROOT_MISSING")
        self._proc = subprocess.Popen(
            [str(binary), "mcp", "--no-gpu",
             "--automation-read-root", str(ARTCRAFT_ROOT / "input"),
             "--automation-write-root", str(ARTCRAFT_ROOT / "output")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=ARTCRAFT_ROOT,
            env={key: os.environ[key] for key in ("HOME", "PATH", "LANG", "TMPDIR")
                 if key in os.environ})
        os.set_blocking(self._proc.stdin.fileno(), False)
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._proc.stdout, selectors.EVENT_READ)
        try:
            self.server = self.rpc("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "kmcw-v3", "version": "1"}})
            self._send(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            listing = self.rpc("tools/list", {})
            if listing.get("nextCursor") or not isinstance(listing.get("tools"), list):
                raise ToolError("MCP_TOOL_LIST_INCOMPLETE")
            for tool in listing["tools"]:
                name = tool.get("name")
                if not isinstance(name, str) or name in self.tools:
                    raise ToolError("MCP_TOOL_LIST_INVALID")
                self.tools[name] = tool
        except Exception:
            self.close()
            raise

    def _send(self, payload):
        deadline = time.monotonic() + self.timeout_s
        with selectors.DefaultSelector() as writable:
            writable.register(self._proc.stdin, selectors.EVENT_WRITE)
            while payload:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not writable.select(remaining):
                    raise ToolError("MCP_TIMEOUT")
                try:
                    count = os.write(self._proc.stdin.fileno(), payload)
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise ToolError("MCP_EOF")
                payload = payload[count:]

    def rpc(self, method, params):
        if self.closed:
            raise ToolError("MCP_CLOSED")
        self._id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self._id,
                              "method": method, "params": params}).encode() + b"\n"
        if len(payload) > 4096:
            raise ToolError("MCP_REQUEST_TOO_LARGE")
        deadline = time.monotonic() + self.timeout_s
        try:
            self._send(payload)
            consumed = 0
            while True:
                if time.monotonic() >= deadline:
                    raise ToolError("MCP_TIMEOUT")
                if b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    reply = json.loads(line)
                    if not isinstance(reply, dict):
                        raise ToolError("MCP_RESULT_INVALID")
                    if reply.get("id") != self._id:
                        if "id" in reply:
                            raise ToolError("MCP_RESPONSE_ID_MISMATCH")
                        continue
                    if "error" in reply:
                        raise ToolError("MCP_RPC_ERROR")
                    result = reply.get("result")
                    if not isinstance(result, dict):
                        raise ToolError("MCP_RESULT_INVALID")
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._selector.select(remaining):
                    raise ToolError("MCP_TIMEOUT")
                chunk = os.read(self._proc.stdout.fileno(), 65536)
                if not chunk:
                    raise ToolError("MCP_EOF")
                consumed += len(chunk)
                self._buffer += chunk
                if consumed > MCP_MAX_BYTES:
                    raise ToolError("MCP_RESPONSE_TOO_LARGE")
        except (ToolError, OSError, ValueError) as exc:
            self.close()
            if isinstance(exc, ToolError):
                raise
            raise ToolError("MCP_TRANSPORT_FAILURE", type(exc).__name__) from None

    def call_tool(self, name, args):
        if name not in self.tools:
            raise ToolError("MCP_TOOL_ABSENT")
        if name not in {"session_list", "doc_inspect", "doc_new", "doc_export"}:
            raise ToolError("MCP_TOOL_NOT_ALLOWLISTED")
        args = dict(args)
        if name == "doc_new":
            if any(type(args.get(k)) is not int or not 1 <= args[k] <= 2048
                   for k in ("width", "height")):
                raise ToolError("ARTCRAFT_SIZE_DENIED")
        if name == "doc_export":
            target = _artcraft_path(ARTCRAFT_ROOT / "output", args.get("path", ""))
            if target.suffix.lower() != ".png" or target.exists():
                raise ToolError("ARTCRAFT_EXPORT_DENIED")
            args["path"] = str(target)
        result = self.rpc("tools/call", {"name": name, "arguments": args})
        if result.get("isError"):
            raise ToolError("MCP_TOOL_ERROR")
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._selector.close()
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        for pipe in (self._proc.stdin, self._proc.stdout):
            pipe.close()


def _kcc_read(kind, args, ctx):
    token = _need(ctx, "kcc_runtime_token")
    if not isinstance(token, str) or not token:
        raise ToolError("KCC_AUTH_MISSING")
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    req = request.Request(KCC_RUNTIME_ENDPOINT, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with request.build_opener(NoRedirect).open(req, timeout=10) as response:
            raw = response.read(MCP_MAX_BYTES + 1)
        if len(raw) > MCP_MAX_BYTES:
            raise ToolError("KCC_RESPONSE_TOO_LARGE")
        snapshot = json.loads(raw)
    except error.HTTPError as exc:
        raise ToolError("KCC_AUTH_DENIED" if exc.code in (401, 403)
                        else "KCC_HTTP_UNAVAILABLE", str(exc.code)) from None
    except (error.URLError, TimeoutError, ValueError):
        raise ToolError("KCC_READ_UNAVAILABLE") from None
    if (not isinstance(snapshot, dict)
            or snapshot.get("component") != "KCC_D1_RUNTIME_V2"
            or snapshot.get("authenticated") is not True
            or snapshot.get("runtime_authority") != "CLOUDFLARE_D1"
            or snapshot.get("spreadsheet_runtime_dependency") is not False):
        raise ToolError("KCC_AUTHORITY_INVALID")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(
            snapshot["generated_at"].replace("Z", "+00:00"))).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise ToolError("KCC_FRESHNESS_INVALID") from None
    if not 0 <= age <= 60:
        raise ToolError("KCC_SNAPSHOT_STALE")
    sources = snapshot.get("source_health")
    if not isinstance(sources, dict) or not sources or any(
            not isinstance(s, dict) or s.get("ok") is not True
            for s in sources.values()):
        raise ToolError("KCC_D1_UNHEALTHY")
    metadata = {"endpoint": KCC_RUNTIME_ENDPOINT, "source": "CLOUDFLARE_D1",
                "generated_at": snapshot["generated_at"], "age_s": age,
                "execution_policy": "READ_ONLY_NO_EXECUTION_CLAIM_NO_STATUS_ASSUMPTION"}
    if kind == "health":
        return {**metadata, "sources": sources}
    table, key = {"task_read": ("tasks", "task_id"),
                  "mission_read": ("missions", "mission_id"),
                  "node_read": ("nodes", "node_id")}[kind]
    if not ID_RE.fullmatch(args[key]):
        raise ToolError("TOOL_ARGS_INVALID", key)
    rows = snapshot.get(table)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ToolError("KCC_READ_MODEL_INVALID")
    matches = [row for row in rows if row.get(key) == args[key]]
    if len(matches) != 1:
        raise ToolError("KCC_RECORD_NOT_IN_BOUNDED_SNAPSHOT")
    return {**metadata, "record": matches[0]}


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
    remote = args.get("remote", "origin")
    branch = args["branch"]
    if not remote or remote.startswith("-") or not branch or branch.startswith("-"):
        raise ToolError("TOOL_PUSH_REF_INVALID")
    rc, out = _run(["git", "-C", repo, "check-ref-format", "--branch", branch],
                   repo)
    if rc != 0:
        raise ToolError("TOOL_PUSH_REF_INVALID", out)
    rc, remotes = _run(["git", "-C", repo, "remote"], repo)
    if rc != 0:
        raise ToolError("TOOL_GIT_FAIL", remotes)
    if remote not in remotes.splitlines():
        raise ToolError("TOOL_REMOTE_NOT_APPROVED", remote[:120])
    rc, out = _run(["git", "-C", repo, "ls-remote", "--heads", "--",
                    remote, f"refs/heads/{branch}"], repo, timeout=60)
    if rc != 0:
        raise ToolError("TOOL_GIT_FAIL", out)
    if not out.strip():
        raise ToolError("BRANCH_CREATE_NOT_APPROVED", branch)
    rc, out = _run(["git", "-C", repo, "push", "--", remote, branch],
                   repo, timeout=120)
    if rc != 0:
        raise ToolError("TOOL_GIT_FAIL", out)
    return {"rc": rc, "output": out[:2000]}


def _pr_create(args: dict) -> dict:
    repo = _repo(args)
    rc, branch = _run(["git", "-C", repo, "branch", "--show-current"], repo)
    branch = branch.strip()
    if rc != 0 or not branch:
        raise ToolError("PR_HEAD_UNAVAILABLE", branch[:300])
    rc, existing = _run(["gh", "pr", "list", "--state", "all", "--head",
                         branch, "--limit", "1000", "--json",
                         "number,headRefName"], repo, timeout=60)
    if rc != 0:
        raise ToolError("TOOL_GH_FAIL", existing)
    try:
        rows = json.loads(existing)
    except ValueError:
        raise ToolError("TOOL_GH_INVALID_RESPONSE") from None
    if (not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)):
        raise ToolError("TOOL_GH_INVALID_RESPONSE")
    if any(row.get("headRefName") == branch for row in rows):
        raise ToolError("PR_ALREADY_EXISTS", branch[:120])
    argv = ["gh", "pr", "create", "--draft", "--head", branch,
            "--title", args["title"]]
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
