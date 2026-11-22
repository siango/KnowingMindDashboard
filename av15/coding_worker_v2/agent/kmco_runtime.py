"""KMCO bounded circuit resolver layered on the existing KMCW ToolRegistry.

Design catalog != executable handler. No database, queue, or server is created.
Attach explicitly only after an authenticated owner selects a pinned catalog.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

OP_ID = re.compile(r"^(?:P|D)\d{2}-M\d{2}$")
GROUP_ID = re.compile(r"^(?:P|D)\d{2}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class KMCOError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Operation:
    op_id: str
    group_id: str
    source_sha256: str
    name: str


class KMCOCatalog:
    """Immutable design snapshot; never treats design as an execution grant."""

    def __init__(self, records, *, source_digests):
        if not isinstance(records, list) or not isinstance(source_digests, dict):
            raise KMCOError("KMCO_CATALOG_INVALID")
        if set(source_digests) != {"core.md", "domain.md"} or any(
            not isinstance(s, str) or not SHA256.fullmatch(s)
            for s in source_digests.values()
        ):
            raise KMCOError("KMCO_SOURCE_DIGEST_INVALID")
        operations = {}
        for row in records:
            if not isinstance(row, dict):
                raise KMCOError("KMCO_RECORD_INVALID")
            op_id, group = row.get("operation_id"), row.get("group_id")
            source_file, sha = row.get("source_file"), row.get("source_sha256")
            cells = row.get("source_cells")
            if (not isinstance(op_id, str) or not OP_ID.fullmatch(op_id)
                    or not isinstance(group, str) or not GROUP_ID.fullmatch(group)
                    or op_id.split("-")[0] != group
                    or source_file not in source_digests
                    or sha != source_digests[source_file]
                    or not isinstance(cells, list) or len(cells) < 2
                    or cells[0] != op_id or not isinstance(cells[1], str)
                    or not cells[1].strip() or op_id in operations):
                raise KMCOError("KMCO_RECORD_INVALID")
            operations[op_id] = Operation(op_id, group, sha, cells[1])
        if len(operations) != 132 or len({o.group_id for o in operations.values()}) != 24:
            raise KMCOError("KMCO_CANONICAL_COUNT_MISMATCH")
        self._operations = operations
        self._source_digests = dict(source_digests)

    @classmethod
    def from_snapshot(cls, root):
        root = Path(root).resolve()
        candidate = root / "kmco-registry-candidate.json"
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 250000:
            raise KMCOError("KMCO_SNAPSHOT_UNAVAILABLE")
        doc = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("status") != "IMPORT_CANDIDATE_ONLY":
            raise KMCOError("KMCO_SNAPSHOT_INVALID")
        digests = {}
        for name in ("core.md", "domain.md"):
            path = root / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 200000:
                raise KMCOError("KMCO_SOURCE_UNAVAILABLE")
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls(doc.get("records"), source_digests=digests)

    def describe(self, op_id):
        op = self._operations.get(op_id)
        if op is None:
            raise KMCOError("KMCO_OP_UNKNOWN")
        return {"operation_id": op.op_id, "group_id": op.group_id,
                "name": op.name, "source_sha256": op.source_sha256,
                "state": "DESIGN_REGISTERED", "callable": False,
                "authority_granted": False}

    def summary(self):
        return {"operations": len(self._operations), "groups": 24,
                "source_sha256": dict(self._source_digests),
                "status": "DESIGN_ONLY", "runtime_ready": False}


def validate_route(catalog, steps):
    """Validate an explicit op-level DAG; never infer group edges as op edges."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise KMCOError("KMCO_ROUTE_SIZE_INVALID")
    nodes = {}
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"op_id", "after"}:
            raise KMCOError("KMCO_STEP_INVALID")
        op_id, after = step["op_id"], step["after"]
        if (not isinstance(op_id, str) or op_id not in catalog._operations
                or op_id in nodes or not isinstance(after, list)
                or any(not isinstance(d, str) for d in after)
                or len(after) != len(set(after))):
            raise KMCOError("KMCO_STEP_INVALID")
        nodes[op_id] = after
    if any(dep not in nodes or dep == op for op, deps in nodes.items() for dep in deps):
        raise KMCOError("KMCO_DEPENDENCY_INVALID")
    pending = set(nodes)
    order = []
    while pending:
        ready = sorted(op for op in pending if set(nodes[op]).issubset(order))
        if not ready:
            raise KMCOError("KMCO_ROUTE_CYCLE")
        order.extend(ready)
        pending.difference_update(ready)
    return order


def dispatch_readonly(registry, catalog, steps, ctx):
    """Execute a bounded circuit using only already-authorized KMCW read handlers.

    Full preflight happens before any handler call. Results never imply QC pass.
    Write, deploy, and destructive operations are deliberately out of scope.
    """
    from .tools import ToolError
    from .capabilities import authorized
    from .ingress import qualified
    from .memory import current_base_commit

    try:
        order = validate_route(catalog, steps)
    except KMCOError as exc:
        raise ToolError(exc.code) from None
    if len(order) > 8 or not qualified(ctx):
        raise ToolError("KMCO_READ_EXECUTION_NOT_AUTHORIZED")
    bindings = ctx.get("kmco_handler_bindings")
    if not isinstance(bindings, dict):
        raise ToolError("KMCO_HANDLER_BINDINGS_MISSING")
    sha = current_base_commit(ctx.get("workspace", ""))
    prepared = []
    contracts = {x["name"]: x for x in registry.command_contract()["commands"]}
    for op in order:
        binding = bindings.get(op)
        if not isinstance(binding, dict) or set(binding) != {"tool", "arguments", "source_sha"}:
            raise ToolError("KMCO_HANDLER_BINDING_INVALID")
        name, args = binding["tool"], binding["arguments"]
        contract = contracts.get(name) if isinstance(name, str) else None
        if (contract is None or name.startswith("kmco.")
                or contract["permission"] != "read"
                or not isinstance(args, dict) or binding["source_sha"] != sha):
            raise ToolError("KMCO_HANDLER_UNQUALIFIED")
        if not authorized(contract, ctx, args):
            raise ToolError("KMCO_HANDLER_SCOPE_DENIED")
        prepared.append((op, name, args))
    results = []
    for op, name, args in prepared:
        result = registry.call(name, args, ctx, mcp=True)
        results.append({"operation_id": op, "handler": name, "result": result})
    return {"status": "EXECUTED_UNVERIFIED", "independent_qc": "NOT_RUN",
            "receipt_verified": False, "operations": results}


def register_kmco_tools(registry, catalog):
    """Opt-in integration; original ToolRegistry performs every MCP ACL check.

    Only design introspection and a validated DAG are exposed. Running handlers
    requires separately qualified bindings and is deliberately not exposed here.
    """
    from .tools import ToolSpec, ToolError

    def inspect(args, ctx):
        try:
            return catalog.describe(args["operation_id"])
        except KMCOError as exc:
            raise ToolError(exc.code) from None

    def plan(args, ctx):
        try:
            order = validate_route(catalog, args["steps"])
        except KMCOError as exc:
            raise ToolError(exc.code) from None
        return {"ordered_operations": order, "status": "PLAN_ONLY",
                "execution_authority": False, "executed": False}

    registry.register(
        ToolSpec("kmco.inspect", "KMCO canonical design lookup",
                 {"operation_id": "str"}, {}, "read", "none",
                 "pinned snapshot; no runtime execution"),
        inspect)
    registry.register(
        ToolSpec("kmco.plan", "KMCO bounded circuit dependency resolution",
                 {"steps": "list"}, {}, "read", "none",
                 "32 steps max; no group-edge extrapolation; no execution"),
        plan)
    registry.register(
        ToolSpec("kmco.run_readonly", "KMCO scoped read-only circuit dispatch",
                 {"steps": "list"}, {}, "read", "read-only existing handlers",
                 "max 8 operations; authenticated per-handler grants; no QC promotion"),
        lambda a, c: dispatch_readonly(registry, catalog, a["steps"], c))

    # Permanent P01 circuit binding to its existing five-step implementation.
    # Registration is opt-in with the integrity-checked catalog, not a grant.
    # The original ToolRegistry still enforces MCP identity, exact arguments,
    # workspace policy and read-only permission before invoking this handler.
    def p01_evidence(args, ctx):
        from .kmco_p01 import run_p01, P01Blocked

        if any(f"P01-M{i:02d}" not in catalog._operations for i in range(1, 6)):
            raise ToolError("KMCO_P01_CATALOG_INCOMPLETE")
        workspace = ctx.get("workspace")
        if not isinstance(workspace, str) or not workspace:
            raise ToolError("KMCO_P01_WORKSPACE_REQUIRED")
        try:
            return run_p01(
                Path(workspace), args["file"], args["quote"],
                args.get("assertions", []),
            )
        except (P01Blocked, OSError, ValueError) as exc:
            # Never turn the failure into a fabricated successful receipt.
            raise ToolError("KMCO_P01_DENIED", type(exc).__name__) from None

    registry.register(
        ToolSpec("kmco.p01.evidence",
                 "Execute canonical P01-M01..M05 against a real workspace file",
                 {"file": "str", "quote": "str"},
                 {"assertions": "list"}, "read", "none",
                 "one real UTF-8 file <=1 MiB, exact quote, no network/write; "
                 "MCP requires issuer-bound tool arguments; no execution receipt"),
        p01_evidence)

    # The existing P02-M01..M05 implementation already performs a real,
    # deterministic five-stage context preservation check. Expose that
    # implementation; do not invent 5 independently deployed MicroOps.
    def p02_resume(args, ctx):
        from .kmco_p02 import run_p02, P02Blocked
        from .memory import current_base_commit
        if any(f"P02-M{i:02d}" not in catalog._operations for i in range(1, 6)):
            raise ToolError("KMCO_P02_CATALOG_INCOMPLETE")
        old, new = args["previous"], args["current"]
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise ToolError("KMCO_P02_CONTEXT_INVALID")
        # Tenant/mission identity MUST come from the bound KMCW issuer, not
        # an untrusted arbitrary command argument.
        if (not isinstance(ctx.get("mission_id"), str)
                or not isinstance(ctx.get("task_id"), str)
                or old.get("mission_id") != ctx["mission_id"]
                or new.get("mission_id") != ctx["mission_id"]
                or old.get("task_id") != ctx["task_id"]
                or new.get("task_id") != ctx["task_id"]):
            raise ToolError("KMCO_P02_ISSUER_SCOPE_MISMATCH")
        try:
            pinned = current_base_commit(ctx["workspace"])
        except (OSError, ValueError, KeyError):
            raise ToolError("KMCO_P02_SOURCE_UNAVAILABLE") from None
        if not isinstance(pinned, str) or not re.fullmatch(r"[0-9a-f]{40}", pinned):
            raise ToolError("KMCO_P02_SOURCE_INVALID")
        if old.get("source_sha") != pinned or new.get("source_sha") != pinned:
            raise ToolError("KMCO_P02_SOURCE_CHANGED")
        try:
            return run_p02(old, new)
        except P02Blocked as exc:
            raise ToolError(str(exc)) from None

    registry.register(
        ToolSpec("kmco.p02.resume",
                 "Run real P02-M01..M05 mission context continuity circuit",
                 {"previous": "dict", "current": "dict"}, {},
                 "read", "none",
                 "requires issuer-bound mission/task + exact Git SHA; "
                 "no state mutation or independent QC promotion"),
        p02_resume)

    # P03 already reuses the bounded queue/retry problem design. This is a
    # deterministic analysis of supplied snapshots, NOT an automatic retry.
    def p03_loop_guard(args, ctx):
        from .kmco_p03 import run_p03, P03Blocked
        if any(f"P03-M{i:02d}" not in catalog._operations for i in range(1, 6)):
            raise ToolError("KMCO_P03_CATALOG_INCOMPLETE")
        if not isinstance(ctx.get("mission_id"), str) or not isinstance(ctx.get("task_id"), str):
            raise ToolError("KMCO_P03_TRUSTED_CONTEXT_REQUIRED")
        try:
            return run_p03(args["attempts"],
                           mission_id=ctx["mission_id"],
                           task_id=ctx["task_id"],
                           max_attempts=args.get("max_attempts", 3))
        except P03Blocked as exc:
            raise ToolError(str(exc)) from None

    registry.register(
        ToolSpec("kmco.p03.loop_guard",
                 "Five-stage bounded retry loop analysis with stop recommendation",
                 {"attempts": "list"}, {"max_attempts": "int"},
                 "read", "none",
                 "P03-M01..M05; caller observations not verified; never "
                 "authorizes retry, mutates queue or grants independence"),
        p03_loop_guard)

    def p04_plan(args, ctx):
        from .kmco_p04 import plan_validator, P04Blocked
        if any(f"P04-M{i:02d}" not in catalog._operations for i in range(1, 6)):
            raise ToolError("KMCO_P04_CATALOG_INCOMPLETE")
        if not isinstance(ctx.get("mission_id"), str) or not isinstance(ctx.get("owner_node"), str):
            raise ToolError("KMCO_P04_ISSUER_REQUIRED")
        try:
            return plan_validator(args["plan"], ctx["mission_id"], ctx["owner_node"])
        except P04Blocked as exc:
            raise ToolError(str(exc)) from None

    registry.register(
        ToolSpec("kmco.p04.plan",
                 "Validate real KM_TASK_PLAN_V1 across five P04 design stages",
                 {"plan": "dict"}, {}, "read", "none",
                 "strict existing planner DAG/scope/mission; no enqueue or authority"),
        p04_plan)

    def p05_completion(args, ctx):
        from .kmco_p05 import run_p05
        if any(f"P05-M{i:02d}" not in catalog._operations for i in range(1, 6)):
            raise ToolError("KMCO_P05_CATALOG_INCOMPLETE")
        return run_p05(args["plan"], ctx)

    registry.register(
        ToolSpec("kmco.p05.completion", "Validate receipt, tests and signed independent proof",
                 {"plan": "dict"}, {}, "read", "none",
                 "original completion_readback; scoped receipt, signature and criteria; no transition"),
        p05_completion)

    def p06_permission(args, ctx):
        from .kmco_p06 import run_p06
        if any(f"P06-M{i:02d}" not in catalog._operations for i in range(1, 6)):
            raise ToolError("KMCO_P06_CATALOG_INCOMPLETE")
        return run_p06(registry, args["tool"], args["arguments"], ctx)

    registry.register(
        ToolSpec("kmco.p06.permission", "Existing tool schema, grant and read scope preflight",
                 {"tool": "str", "arguments": "dict"}, {}, "read", "none",
                 "no target dispatch; same capabilities.authorized gate; privileged calls use original tool"),
        p06_permission)

    def core_recall(args, ctx):
        from .kmco_core import recall
        try:
            return recall(ctx, limit=args.get("limit", 10))
        except RuntimeError as exc:
            # Public diagnostics are fixed error codes, not DB/secret strings.
            raise ToolError(str(exc)) from None

    registry.register(
        ToolSpec("kmco.core.recall",
                 "Read bounded same-mission/task KEBUNATE four-part work memory",
                 {}, {"limit": "int"}, "read", "none",
                 "uses existing km_microops and libpq service only; "
                 "retrieval is not verification or authority"),
        core_recall)
