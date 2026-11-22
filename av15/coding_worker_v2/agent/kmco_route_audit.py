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
# Alias K01-K12 from the core design map P01-P12: do not interpret
# a design group dependency as a currently deployed callable.
CORE_GROUPS = {f"K{i:02d}": f"P{i:02d}" for i in range(1, 13)}
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
    }
    rows = []
    for rid, nodes in ROUTES.items():
        core = rid <= "R05"
        mapped = [CORE_GROUPS[n] if core else n for n in nodes]
        actual = {group: source_handlers[group]
                  for group in set(mapped)
                  if group in source_handlers and source_handlers[group] in commands}
        unbound = sorted(set(mapped) - set(actual))
        rows.append({
            "route_id": rid,
            "design_groups": list(nodes),
            "source_group_bindings": actual,
            "unbound_groups": unbound,
            "source_coverage": "PARTIAL" if actual else "NO_VERIFIED_BINDINGS",
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
        "bound_source_handlers": {
            group: tool for group, tool in source_handlers.items() if tool in commands
        },
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
