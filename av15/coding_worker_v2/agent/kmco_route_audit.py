"""Canonical R01-R13 KMCO route coverage; never confuses design with runtime.

No dispatch, no secrets, no database or network calls. Source route templates
match the original PR #1 registry comments 6996804410 and 6996899236.
"""
from __future__ import annotations

from .kmco_runtime import KMCOError

ROUTES = {
    "R01": ("K01", "K05", "K04", "K02", "K03", "K09", "K06", "K10", "K05"),
    "R02": ("K01", "K08", "K07", "K09", "K06", "K10"),
    "R03": ("K01", "K05", "K04", "K10"),
    "R04": ("K01", "K03", "K12", "K06", "K10"),
    "R05": ("K02", "K03", "K09", "K10", "K05"),
    "R06": ("D08", "D01", "D02", "D09"),
    "R07": ("D08", "D01", "D03", "D11", "D09"),
    "R08": ("D08", "D04", "D11", "D09"),
    "R09": ("D08", "D05", "D03", "D09"),
    "R10": ("D08", "D06", "D07", "D09"),
    "R11": ("D08", "D07", "D10", "D09"),
    "R12": ("D08", "D01", "D10", "D11", "D09"),
    "R13": ("D01", "D12", "D11", "D09"),
}
# K01-K12 are capabilities, not aliases of P01-P12 problem circuits.
# A separate source-to-capability proof is required before route execution.
REQUIRES_PRIVILEGED = {"R01", "R02", "R05", "R07", "R08", "R09", "R12", "R13"}


def audit_routes(catalog, registry, ctx):
    """Emit missing real bindings by route; NEVER mark E2E verified from config."""
    if catalog is None or registry is None or not isinstance(ctx, dict):
        raise KMCOError("KMCO_ROUTE_AUDIT_CONTEXT_REQUIRED")
    groups = {op.group_id for op in catalog._operations.values()}
    expected = {f"P{i:02d}" for i in range(1, 13)} | {
        f"D{i:02d}" for i in range(1, 13)}
    if groups != expected or len(catalog._operations) != 132:
        raise KMCOError("KMCO_ROUTE_AUDIT_DESIGN_MISMATCH")
    commands = {c["name"]: c for c in registry.command_contract()["commands"]}
    # The actual retained native implementations cover only these source
    # groups at this checkpoint; never fabricate a full group binding.
    source_handlers = {
        "P01": "kmco.p01.evidence",
        "P02": "kmco.p02.resume",
        "P03": "kmco.p03.loop_guard",
        "P04": "kmco.p04.plan",
        "P05": "kmco.p05.completion",
        "P06": "kmco.p06.permission",
        "P07": "kmco.p07.checkpoint",
        "P08": "kmco.p08.memory",
        "P09": "kmco.p09.lease",
        "P10": "kmco.p10.route",
        "P11": "kmco.p11.security",
        "P12": "kmco.p12.providers",
        "D01": "kmco.d01.authority",
        "D02": "kmco.d02.read",
        "D12": "kmco.d12.backup_restore",
    }
    # Preview-only commands implement bounded subsets, never whole D-groups.
    # This inventory is intentionally separate from source_handlers and MUST
    # NOT change native_ops, design_only, route unbound, E2E or QC totals.
    preview_candidates = {
        "D06": "kmco.d06.question_preview",
        "D07": "kmco.d07.memory_preview",
        "D08": "kmco.d08.preflight",
        "D09": "kmco.d09.preflight",
        "D10": "kmco.d10.drift_preview",
        "D11": "kmco.d11.observe",
    }
    partial_preview_bindings = {
        group: tool for group, tool in preview_candidates.items()
        if tool in commands and commands[tool]["permission"] == "read"
    }
    operations = []
    native_ops = 0
    for op_id, item in sorted(catalog._operations.items()):
        bound = source_handlers.get(item.group_id)
        registered = bool(bound and bound in commands)
        native_ops += int(registered)
        operations.append({
            "operation_id": op_id,
            "group_id": item.group_id,
            "design_name": item.name,
            "source_sha256": item.source_sha256,
            "source_handler": bound if registered else None,
            "partial_preview_handler": partial_preview_bindings.get(item.group_id),
            "source_state": ("NATIVE_HANDLER_REGISTERED_NOT_LIVE_VERIFIED"
                             if registered else "DESIGN_ONLY"),
            "actual_tools_call": "NOT_TESTED",
            "independent_qc": "NOT_RUN",
        })
    rows = []
    for rid, nodes in ROUTES.items():
        actual = {g: source_handlers[g] for g in nodes
                  if g.startswith("D") and source_handlers.get(g) in commands}
        unbound = sorted(set(nodes) - set(actual))
        rows.append({
            "route_id": rid,
            "design_groups": list(nodes),
            "source_group_bindings": actual,
            "unbound_groups": unbound,
            "partial_preview_groups": sorted(set(nodes) & set(partial_preview_bindings)),
            "source_coverage": ("PARTIAL_SOURCE_BINDINGS_NOT_E2E" if actual
                                else "NO_VERIFIED_ROUTE_BINDINGS"),
            "live_handler_execution": "NOT_TESTED",
            "authority": "NOT_GRANTED_BY_AUDIT",
            "independent_qc": "NOT_RUN",
            "production_pass": False,
            "action_class": "GATED" if rid in REQUIRES_PRIVILEGED else "READ_OR_ANSWER",
        })
    return {
        "schema": "KMCO_R01_R13_RUNTIME_COVERAGE_V1",
        "source": "EXISTING_CANONICAL_PR1_REGISTRY",
        "design_route_count": len(rows),
        "design_operations": len(catalog._operations),
        "native_operation_source_bound": native_ops,
        "design_only_operations": len(operations) - native_ops,
        "operation_coverage": operations,
        "native_problem_circuit_handlers": {
            group: tool for group, tool in source_handlers.items()
            if group.startswith("P") and tool in commands
        },
        "native_domain_circuit_handlers": {
            group: tool for group, tool in source_handlers.items()
            if group.startswith("D") and tool in commands
        },
        "partial_preflight_bindings": partial_preview_bindings,
        "verified_end_to_end_routes": 0,
        "status": "COVERAGE_ONLY_NOT_E2E",
        "routes": rows,
        "external_providers": {
            "cloudflare": "NOT_TESTED",
            "stripe": "NOT_TESTED",
            "binance": "NOT_TESTED",
            "dhamma_mcp": "NOT_TESTED",
        }
    }
