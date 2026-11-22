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
import re
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
import inspect
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
    output_schema: dict = field(default_factory=lambda: {"type": "object"})
    required_permissions: tuple = ()
    audit: str = "ToolRegistry.call outcome digest and policy evidence"
    source_ref: str = ""
    backend_ref: str = ""


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
                or not isinstance(spec.output_schema, dict)
                or not isinstance(spec.required_permissions, tuple)
                or any(not isinstance(p, str) or not p for p in spec.required_permissions)
                or set(spec.required) & set(spec.optional)
                or any(not isinstance(key, str) or not key
                       or type_name not in TYPE_MAP
                       for mapping in (spec.required, spec.optional)
                       for key, type_name in mapping.items())
                or not callable(fn)):
            raise ToolError("TOOL_SPEC_INVALID", str(spec.name))
        # Describe the existing handler and its configuration dependency. These
        # locators are not connections, credentials or execution grants.
        name = spec.name
        if name.startswith('db.'):
            backend = 'database_bindings:system=' + name.split('.')[1]
        elif name.startswith('kmcw.'):
            backend = 'command_channels + command_auth; existing KMCW handlers'
        elif name.startswith('kcc.'):
            backend = ('km_admin_binding.origin + existing session_cookie; GET ' +
                       ('/v1/control/backend-read-health; bounded project.health read'
                        if name.endswith('.health') else '/v1/control/overview; projection only'))
        elif name.startswith('serena.'):
            backend = 'build_default_registry(serena): existing qualified session'
        elif name == 'gui_test':
            backend = 'gui_binding.run + evidence_path + screenshot_path'
        elif name.startswith('pilot_') or name == 'account_work':
            backend = 'pilot configuration + existing QueueStore/Memory/receipts'
        elif name in {'queue_health', 'task_enqueue'}:
            backend = 'context.store:QueueStore'
        elif name == 'receipt_read':
            backend = 'context.receipts_dir'
        elif name == 'ollama_inventory':
            backend = 'context.config.ollama_url; loopback required'
        elif name in {'ci_status', 'pr_checks', 'pr_create'}:
            backend = 'existing gh executable and repository authentication'
        else:
            backend = 'existing workspace handler:' + name
        spec = replace(spec, source_ref=spec.source_ref or
                       f'{getattr(fn, "__module__", type(fn).__module__)}:'
                       f'{getattr(fn, "__qualname__", type(fn).__qualname__)}',
                       backend_ref=spec.backend_ref or backend)
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
             "source_ref":spec.source_ref, "backend_ref":spec.backend_ref,
             "limits":spec.limits, "output_schema":spec.output_schema,
             "input_schema":{"type":"object", "additionalProperties":False,
                "required":list(spec.required), "properties":{
                    k:{"type":{"str":"string","int":"integer","bool":"boolean",
                                "list":"array","dict":"object"}[v]}
                    for k,v in {**spec.required, **spec.optional}.items()}},
             "required_permissions":list(spec.required_permissions), "audit":spec.audit,
             "health":"registered probe" if spec.name in self._health else "local handler",
             "evidence":"tool_called outcome digest plus policy evidence"}
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
            try:
                source_file = inspect.getsourcefile(self._fns[name])
            except TypeError:
                try:
                    source_file = inspect.getsourcefile(type(self._fns[name]))
                except TypeError:
                    source_file = None
            out.append({"tool": name, "capability": spec.capability,
                        "source_ref": spec.source_ref,
                        "source_file": source_file,
                        "backend_ref": spec.backend_ref,
                        "permission": spec.permission, "available": available,
                        "reason": redact(reason),
                        "required": dict(spec.required),
                        "optional": dict(spec.optional),
                        "side_effects": spec.side_effects, "limits": spec.limits,
                        "output_schema": spec.output_schema,
                        "required_permissions": list(spec.required_permissions),
                        "audit": spec.audit})
        return out

    def call(self, name: str, args: dict, ctx: dict, *, mcp=False) -> dict:
        started = time.monotonic()
        success = False
        result_hash, error_class = None, None
        ctx.pop(f"_{name}_ok", None)  # prevent stale success from a prior call
        ctx.pop('work_memory', None)
        try:
            result = self._call_inner(name, args, ctx, mcp=mcp)
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
            # KMCO events are durable only with an owner-provisioned libpq
            # service. Never silently promote in-memory evidence into a DB
            # receipt. A configured but broken sink fails closed.
            if name.startswith("kmco."):
                from .kmco_usage import record as record_kmco_usage
                try:
                    usage_state = record_kmco_usage(
                        name, args, ctx, ok=success,
                        result_sha256=result_hash, error_class=error_class)
                except RuntimeError as exc:
                    if isinstance(evidence, list):
                        evidence.append({"event": "kmco_usage_audit",
                                         "tool": name, "state": str(exc),
                                         "durable": False})
                    raise ToolError(str(exc)) from None
                if isinstance(evidence, list):
                    evidence.append({"event": "kmco_usage_audit",
                                     "tool": name, "state": usage_state,
                                     "durable": usage_state == "PERSISTED_UNVERIFIED"})

    def _call_inner(self, name: str, args: dict, ctx: dict, *, mcp=False) -> dict:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolError("TOOL_UNKNOWN", name[:80])
        if mcp:
            from .pilot import TOOLS
            from .ingress import CATALOG
            from .capabilities import authorized
            local_pilot = (name in TOOLS and 'pilot' in ctx
                and not any(k in ctx for k in ('command_channels', 'command_auth',
                                               'database_auth', 'tool_bindings')))
            # Command envelopes and database adapters enforce their own scoped
            # identities, operation grants and approvals in the existing handler.
            if not local_pilot and name not in CATALOG and not name.startswith('db.'):
                contract = next(s for s in self.command_contract()['commands']
                                if s['name'] == name)
                if not authorized(contract, ctx, args):
                    raise ToolError('MCP_TOOL_SCOPE_DENIED')
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
                  "checkpoint" if name.startswith(("kmcw.", "db.")) and spec.permission != 'read' else
                  "checkpoint" if name.startswith('kmcp.') and spec.permission == 'write' else
                  "checkpoint" if name in {"task_enqueue", "pilot_submit", "pilot_run",
                                           "pilot_discuss", "pilot_reconcile", "gui_test", "cf.ai.run"} else
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
        # Reuse original work memory only after this request passes the existing
        # policy gates. Retrieval never authorizes a tool or reuses its result.
        # Limit this handoff to the authorized local operator interface.
        # An MCP tool grant is not a grant to scan receipt memory. Other
        # handlers retain their own memory and deferred authority boundaries.
        if (not mcp and name in {'repo_inspect', 'code_search'}
                and all(ctx.get(k) is not None for k in
                        ('workspace', 'store', 'receipts_dir'))):
            from .memory import current_base_commit, retrieve_work
            cases = retrieve_work(name, workspace, current_base_commit(workspace),
                ctx['store'], Path(ctx['receipts_dir']),
                account_id=ctx.get('account_id'))
            ctx['work_memory'] = cases
            ctx.setdefault('evidence', []).append({'event':'memory_retrieved',
                'tool':name, 'cases':len(cases), 'grants_authority':False,
                'evidence_refs':[case.get('provenance', {}) for case in cases]})
        ctx.setdefault('evidence', []).append({'event':'tool_selected',
            'tool':name, 'selection':'EXPLICIT_REQUEST_AFTER_POLICY_CHECKS',
            'registry_sha256':self.command_contract()['sha256'],
            # Supplied V4 context does not trigger a remote read or acquire
            # memory permission from a tool grant. Keep source status intact.
            'memory_context_sha256':hashlib.sha256(json.dumps(
                ctx['memory_context'],sort_keys=True,separators=(',', ':'),
                ensure_ascii=False).encode()).hexdigest()
                if isinstance(ctx.get('memory_context'),dict) else None})
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
                 "--sort", "path",
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


def build_default_registry(photocraft=None, serena=None, kmco_catalog=None,
                           *, circuits=False, cloudflare=None) -> ToolRegistry:
    reg = ToolRegistry()
    from .pilot import register_pilot_tools
    register_pilot_tools(reg)
    from .ingress import register as register_ingress
    register_ingress(reg)

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
    # Reuse an existing qualified Serena session; never launch/install one here.
    # Only bounded symbol reads are exposed. No edit, memory or shell forwarding.
    def serena_health(ctx):
        if serena is None or getattr(serena, "closed", True):
            return False, "SERENA_READ_ONLY_SESSION_UNAVAILABLE"
        if (getattr(serena, "read_only_qualified", False) is not True
                or not isinstance(getattr(serena, "identity_sha256", None), str)
                or not re.fullmatch(r"[0-9a-f]{64}", serena.identity_sha256)
                or Path(getattr(serena, "workspace", "")).resolve()
                != Path(ctx.get("workspace", "")).resolve()):
            return False, "SERENA_READ_ONLY_QUALIFICATION_REQUIRED"
        return True, "QUALIFIED_SESSION_CLAIMS_REQUIRE_TRUSTED_BINDING"

    def serena_read(args, ctx, remote):
        relative = args["relative_path"]
        root = Path(ctx["workspace"]).resolve()
        path = root / relative
        approval = policy_decision("inspect", ctx.get("task_id", "operator"),
                                   str(root), paths=[relative])
        if (approval["decision"] != "AUTO_APPROVE" or not path.is_file()
                or path.stat().st_size > MCP_MAX_BYTES
                or any(root.joinpath(*Path(relative).parts[:i]).is_symlink()
                       for i in range(1, len(Path(relative).parts) + 1))):
            raise ToolError("SERENA_SCOPE_DENIED")
        if remote not in getattr(serena, "tools", {}):
            raise ToolError("SERENA_TOOL_NOT_DISCOVERED")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        forwarded = {**args, "max_answer_chars": 8000}
        if remote == "find_symbol":
            pattern = args["name_path_pattern"]
            if not pattern.strip() or len(pattern) > 200:
                raise ToolError("SERENA_SYMBOL_PATTERN_INVALID")
            forwarded.update(include_body=False, depth=0)
        result = serena.call_tool(remote, forwarded)
        if hashlib.sha256(path.read_bytes()).hexdigest() != before:
            raise ToolError("SERENA_READ_MUTATED_SOURCE")
        if not isinstance(result, dict) or result.get("isError"):
            raise ToolError("SERENA_TOOL_ERROR")
        if len(json.dumps(result).encode()) > 16000:
            raise ToolError("SERENA_RESULT_TOO_LARGE")
        return {"result": result, "source_sha256": before, "read_only": True,
                "tool": remote, "adapter_identity_sha256": serena.identity_sha256,
                "runtime_authority": False}

    for local, remote, required in (
        ("symbol_overview", "get_symbols_overview", {"relative_path": "str"}),
        ("find_symbol", "find_symbol", {"relative_path": "str", "name_path_pattern": "str"}),
    ):
        reg.register(ToolSpec("serena." + local, "existing Serena symbol read",
                              required, {}, "read", "none",
                              "qualified existing session only; file scope; 8000 chars; no edits/shell"),
                     lambda a, c, remote=remote: serena_read(a, c, remote), serena_health)
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
            f"kcc.d1.{kind}", ("KM AI Admin bounded backend read health (legacy tool ID)"
                              if kind == "health" else f"KM AI Admin {kind} read-only projection (legacy tool ID)"),
            {field_name: "str"} if field_name else {}, {}, "read", "none",
            "KM AI Admin bounded read contract; no execution authority; deployment must be verified",
            source_ref=KM_ADMIN_SOURCE_REF),
            lambda a, c, kind=kind: _kcc_read(kind, a, c),
            lambda c, kind=kind: _km_admin_health(kind, c))
    from .database_tools import register_database_tools
    register_database_tools(reg)
    from .capabilities import register_gui
    register_gui(reg)
    # KMCO attaches only when the owner supplies an integrity-checked catalog.
    # Default registry / 67-tool contract remains unchanged.
    if kmco_catalog is not None:
        from .kmco_runtime import register_kmco_tools
        register_kmco_tools(reg, kmco_catalog)
    if circuits:
        from .circuits import register as register_circuits
        register_circuits(reg)
    if cloudflare is not None:
        from .cloudflare_tools import register as register_cloudflare
        register_cloudflare(reg, cloudflare)
    return reg


ARTCRAFT_ROOT = Path.home() / ".local/share/knowingmind/artcraft"
KM_ADMIN_SOURCE_REF = "https://github.com/siango/KnowingMindApp/blob/7707371a9a4da9126eb70a409b1f1592bd44cf83/cloudflare_workers/knowingmind-line-webhook/admin_control.js"
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


def _km_admin_health(kind, ctx):
    from .ingress import qualified
    if not qualified(ctx) or ctx['command_auth'].get('role') not in {'OWNER', 'ADMIN'}:
        return False, 'KM_ADMIN_OWNER_ADMIN_SESSION_REQUIRED'
    binding = ctx.get('km_admin_binding', {})
    if binding.get('qualified') is not True:
        return False, 'KM_ADMIN_QUALIFIED_ORIGIN_SESSION_BINDING_REQUIRED'
    from urllib.parse import urlsplit
    try:
        origin = urlsplit(binding.get('origin', ''))
        if (origin.scheme != 'https' or not origin.hostname or origin.username
                or origin.password or origin.query or origin.fragment
                or origin.path not in {'', '/'}):
            return False, 'KM_ADMIN_ORIGIN_INVALID'
    except (ValueError, TypeError):
        return False, 'KM_ADMIN_ORIGIN_INVALID'
    cookie = binding.get('session_cookie')
    if not isinstance(cookie, str) or not re.fullmatch(r'__Host-km_web=[a-f0-9]{64}', cookie):
        return False, 'KM_ADMIN_EXISTING_SESSION_COOKIE_REQUIRED'
    return True, 'KM_ADMIN_PROJECTION_BINDING_REMOTE_NOT_PROBED'


def _kcc_read(kind, args, ctx):
    """Compatibility locator/IDs; only current KM AI Admin is contacted.

    Uses the existing authenticated session, never the retired KCC bearer.
    Overview is a projection and cannot admit, dispatch or verify execution.
    """
    from .capabilities import authorized
    spec = {'name': f'kcc.d1.{kind}', 'permission': 'read', 'required_permissions': []}
    if not authorized(spec, ctx, args):
        raise ToolError('MCP_TOOL_SCOPE_DENIED')
    available, reason = _km_admin_health(kind, ctx)
    if not available:
        raise ToolError('TOOL_UNAVAILABLE', reason)
    key = {'task_read': 'task_id', 'mission_read': 'mission_id', 'node_read': 'node_id'}.get(kind)
    if key and not ID_RE.fullmatch(args.get(key, '')):
        raise ToolError('TOOL_ARGS_INVALID', key)
    binding = ctx['km_admin_binding']
    endpoint = binding['origin'].rstrip('/') + (
        '/v1/control/backend-read-health' if kind == 'health' else '/v1/control/overview')
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    req = request.Request(endpoint, method='GET', headers={
        'Cookie': binding['session_cookie'], 'Accept': 'application/json',
        'Cache-Control': 'no-store'})
    try:
        with request.build_opener(NoRedirect).open(req, timeout=10) as response:
            raw = response.read(MCP_MAX_BYTES + 1)
        if len(raw) > MCP_MAX_BYTES:
            raise ToolError('KM_ADMIN_RESPONSE_TOO_LARGE')
        snapshot = json.loads(raw)
    except error.HTTPError as exc:
        raise ToolError('KM_ADMIN_AUTH_DENIED' if exc.code in (401, 403)
                        else 'KM_ADMIN_HTTP_UNAVAILABLE', str(exc.code)) from None
    except (error.URLError, TimeoutError, ValueError):
        raise ToolError('KM_ADMIN_READ_UNAVAILABLE') from None
    if kind == 'health':
        if (not isinstance(snapshot, dict)
                or snapshot.get('schema') != 'KM_AI_ADMIN_BACKEND_READ_HEALTH_V1'
                or snapshot.get('status') != 'READ_VERIFIED'
                or snapshot.get('backend_read_verified') is not True
                or snapshot.get('execution_authority') is not False):
            raise ToolError('KM_ADMIN_BACKEND_HEALTH_CONTRACT_INVALID')
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(
                snapshot['observed_at'].replace('Z', '+00:00'))).total_seconds()
        except (KeyError, TypeError, ValueError):
            raise ToolError('KM_ADMIN_FRESHNESS_INVALID') from None
        if not 0 <= age <= 60:
            raise ToolError('KM_ADMIN_BACKEND_HEALTH_STALE')
        return {'source': 'KM_AI_ADMIN_BOUNDED_BACKEND_READ',
                'status': 'READ_VERIFIED', 'backend_scope': 'project.health',
                'observed_at': snapshot['observed_at'], 'execution_authority': False,
                'limitations': 'One project.health read; not fleet/database-wide health'}
    if (not isinstance(snapshot, dict) or snapshot.get('projection_only') is not True
            or snapshot.get('authority') is not False):
        raise ToolError('KM_ADMIN_PROJECTION_CONTRACT_INVALID')
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(
            snapshot['observed_at'].replace('Z', '+00:00'))).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise ToolError('KM_ADMIN_FRESHNESS_INVALID') from None
    if not 0 <= age <= 60:
        raise ToolError('KM_ADMIN_OVERVIEW_STALE')
    # Pinned control.py:_mission_rows exposes task_id inside missions.
    table = {'task_read': 'missions', 'mission_read': 'missions', 'node_read': 'runtimes'}[kind]
    rows = snapshot.get(table)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ToolError('MISSING_CONTRACT', f'KM AI Admin overview.{table}')
    matches = [r for r in rows if r.get(key) == args[key]]
    if len(matches) != 1:
        raise ToolError('KM_ADMIN_PROJECTED_RECORD_MISSING_OR_AMBIGUOUS')
    return {'record': matches[0], 'source': 'KM_AI_ADMIN_OVERVIEW_PROJECTION',
            'source_ref': KM_ADMIN_SOURCE_REF, 'observed_at': snapshot['observed_at'],
            'projection_only': True, 'execution_authority': False,
            'row_freshness': 'NOT_INFERRED_FROM_OVERVIEW_TIMESTAMP'}


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


