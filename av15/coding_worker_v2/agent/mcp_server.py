"""Bounded newline-delimited MCP stdio transport over ToolRegistry."""
from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

from .pilot import TOOLS
from .tools import ToolError, build_default_registry

MAX_MESSAGE = 1_000_000
VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def prepare_execution_checkpoint(payload, *, binding, principal):
    """Validate a proposed blocked checkpoint; never persist or expose a tool.

    Future transport must supply binding from owner-approved configuration and
    principal from authenticated middleware, never from tool arguments. This
    preparation contract is not authentication, QC, admission or sync proof.
    Existing candidate write denial and MCP tool allowlist remain in force.
    """
    def fail(reason):
        raise ToolError(reason)

    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    fields = {"mission_id", "task_id", "owner_node", "source_sha",
              "worker_source_sha", "scope_digest", "plan_digest"}
    if not isinstance(binding, dict) or set(binding) != fields:
        fail("CHECKPOINT_BINDING_UNAVAILABLE")
    if (binding["owner_node"] != "AV15"
            or any(not isinstance(binding[k], str) or not binding[k] for k in fields)
            or any(not re.fullmatch(r"[0-9a-f]{40}", binding[k])
                   for k in ("source_sha", "worker_source_sha"))
            or not re.fullmatch(r"[0-9a-f]{64}", binding["plan_digest"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", binding["scope_digest"])):
        fail("CHECKPOINT_BINDING_INVALID")
    identity = hashlib.sha256(canonical(binding)).hexdigest()
    # These claims are trusted inputs only when supplied by real middleware.
    if (not isinstance(principal, dict)
            or principal.get("authenticated") is not True
            or not isinstance(principal.get("subject"), str)
            or not principal["subject"].strip()
            or principal.get("owner_node") != "AV15"
            or principal.get("checkpoint_id") != identity
            or principal.get("permissions") != ["checkpoint:append"]):
        fail("CHECKPOINT_WRITER_NOT_AUTHORIZED")
    expected = fields | {"status", "qc", "receipt", "evidence_refs"}
    if not isinstance(payload, dict) or set(payload) != expected:
        fail("CHECKPOINT_PAYLOAD_INVALID")
    if any(payload[k] != binding[k] for k in fields):
        fail("CHECKPOINT_SCOPE_MISMATCH")
    if (payload["status"] != "ADMIT_BLOCKED" or payload["qc"] != "NOT_RUN"
            or payload["receipt"] is not None):
        fail("CHECKPOINT_AUTHORITY_PROMOTION_DENIED")
    refs = payload["evidence_refs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 16:
        fail("CHECKPOINT_IMMUTABLE_EVIDENCE_REQUIRED")
    normalized = []
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {"uri", "sha256"}
                or not isinstance(ref["uri"], str) or not ref["uri"].strip()
                or len(ref["uri"]) > 1000
                or not isinstance(ref["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])):
            fail("CHECKPOINT_IMMUTABLE_EVIDENCE_REQUIRED")
        normalized.append(dict(ref))
    if len({ref["uri"] for ref in normalized}) != len(normalized):
        fail("CHECKPOINT_DUPLICATE_EVIDENCE_URI")
    normalized.sort(key=lambda ref: ref["uri"])
    record = {**binding, "status": "ADMIT_BLOCKED", "qc": "NOT_RUN",
              "receipt": None, "evidence_refs": normalized}
    digest = hashlib.sha256(canonical(record)).hexdigest()
    return {"schema": "KMCW_EXECUTION_CHECKPOINT_PREPARATION_V1",
            "checkpoint_id": identity, "content_sha256": digest,
            "idempotency_key": hashlib.sha256(
                f"{identity}:{digest}".encode("ascii")).hexdigest(),
            "writer_subject": principal["subject"], "record": record,
            "central_sync": "UNSYNCED", "persisted": False,
            "independent_verification_required": True,
            "independent_verified": False,
            "writer_requirement": "Authenticated append-only writer must verify evidence bytes against hashes, enforce fencing and dedup, and return matching authenticated readback; conflict must never overwrite history."}


def _schemas(registry, ctx=None):
    from .ingress import CATALOG
    # ToolRegistry owns the contracts; MCP only adds transport-specific detail.
    contracts = registry.command_contract()["commands"]
    by_name = {spec["name"]: spec for spec in contracts}
    schemas = [{
        "name": spec["name"], "description": spec["capability"] + ". " + spec["limits"],
        "_meta": {"kmcw/capability": {key: spec[key] for key in
            ("permission", "required_permissions", "side_effects", "limits", "audit",
             "source_ref", "backend_ref")}},
        "inputSchema": spec["input_schema"],
    } for spec in contracts]
    from .ingress import command_schema
    for item in schemas:
        if item['name'].startswith('kmcp.'):
            from .circuits import input_schema
            item['inputSchema'] = input_schema(item['name'], item['inputSchema'])
        if item['name'] in CATALOG:
            item['inputSchema']['properties']['command'] = command_schema(item['name'])
        if item['name'].startswith(('db.', 'kmcp.')):
            spec = by_name[item['name']]
            item['outputSchema'] = spec['output_schema']
    return schemas


def _call(registry, params, ctx):
    exposed = {tool['name'] for tool in _schemas(registry, ctx)}
    if not isinstance(params, dict) or params.get("name") not in exposed:
        raise ToolError("PILOT_TOOL_NOT_EXPOSED")
    # Missing audit dependencies must fail before any handler can run. Never
    # manufacture a store/account or let a finally-block KeyError kill stdio.
    from ..queue_store import QueueStore
    if not isinstance(ctx.get('store'), QueueStore):
        raise ToolError('TOOL_UNAVAILABLE', 'MCP_AUDIT_STORE_UNAVAILABLE: context.store requires existing QueueStore')
    if not isinstance(ctx.get('account_id'), str) or not ctx['account_id'].strip():
        raise ToolError('TOOL_UNAVAILABLE', 'MCP_AUDIT_ACCOUNT_REQUIRED: context.account_id')
    ctx["evidence"] = []
    try:
        return registry.call(params["name"], params.get("arguments", {}), ctx, mcp=True)
    finally:
        with ctx["store"]._locked():
            saved = ctx["store"].load_checkpoint(".tool-audit")
            if saved is None and (ctx["store"].checkpoints / ".tool-audit.json").exists():
                raise ValueError("tool approval audit unreadable")
            events = (saved or {}).get("data", {}).get("events", [])
            ctx["store"].save_checkpoint(".tool-audit", "tool_policy_audit", {
                "events": events + [{**event, "account_id": ctx["account_id"]}
                                   for event in ctx["evidence"]]})


def _catalog(registry, ctx, schemas):
    """Account for every registered capability without granting call access.

    The standard tools array exposes definitions, never permission grants.
    Catalog metadata is descriptive: argument scope, freshness, policy and
    handler permission checks still run on every tools/call.
    """
    from .capabilities import authorized
    from .ingress import CATALOG, qualified
    advertised = {item['name'] for item in schemas}
    inputs = {item['name']: item['inputSchema'] for item in schemas}
    observed_at = datetime.now(timezone.utc).isoformat()
    evidence_path = Path(__file__).resolve().parents[2] / 'state/GO-G1-mcp-v5-integration.json'
    try:
        observation = json.loads(evidence_path.read_text()).get('metadata_observation', {})
    except (OSError, ValueError):
        observation = {}
    health = {row['tool']: row for row in registry.discover(ctx)}
    rows = []
    for spec in registry.command_contract()['commands']:
        name = spec['name']
        ready = health[name]
        if name in ctx.get('tool_bindings', {}):
            permitted = authorized(spec, ctx)
        elif name in CATALOG and qualified(ctx):
            auth = ctx.get('command_auth', {})
            role = ctx.get('command_channels', {}).get(auth.get('channel'), {}).get(
                'principals', {}).get(auth.get('principal_id'), {})
            permitted = (name in auth['permissions']
                and auth.get('account_id') == ctx.get('account_id') == role.get('account_id')
                and auth.get('mission_id') == ctx.get('mission_id')
                and ctx.get('mission_id') in role.get('mission_ids', [])
                and bool(ctx.get('task_id'))
                and ctx['task_id'] in auth.get('task_ids', [])
                and ctx['task_id'] in role.get('task_ids', []))
        elif name in CATALOG:
            permitted = False
        elif name.startswith('db.'):
            # Existing DB health includes target/operation authentication and ACL.
            permitted = bool(ready['available'])
        else:
            # Preserve local Pilot compatibility without claiming an
            # unconfigured or remote session has a qualified local identity.
            permitted = (name in TOOLS and 'pilot' in ctx
                and not any(k in ctx for k in ('command_channels', 'command_auth',
                                               'database_auth', 'tool_bindings')))
        if ctx.get('read_only') and spec['permission'] != 'read':
            permitted = False
        callable_now = name in advertised and permitted and bool(ready['available'])
        reason = (None if callable_now else
                  ready['reason'] if not ready['available'] else
                  'TASK_ACL_OR_BINDING_REQUIRED' if not permitted else
                  'MCP_TOOL_NOT_EXPOSED')
        missing = []
        if name.startswith('db.'):
            system = name.split('.')[1]
            bindings = [b for b in ctx.get('database_bindings', {}).values()
                        if b.get('system') == system]
            binding_available = bool(bindings) and bool(ready['available'])
            if not bindings:
                missing.append('database_bindings qualified target/adapter/authority_ref')
            if not ctx.get('database_auth', {}).get('authenticated'):
                missing.append('database_auth authenticated principal and target operation ACL')
            related = observation.get('dhamma_readback') if system in {'postgres', 'pgvector'} else None
        else:
            binding_available = bool(ready['available']) and (
                name in ctx.get('tool_bindings', {}) or
                name in TOOLS and 'pilot' in ctx or
                name in CATALOG and qualified(ctx))
            related = observation.get('gui_evidence') if name == 'gui_test' else None
            if not permitted:
                missing.append('authenticated principal + mission/task ACL + source digest + allowed arguments')
            if not binding_available:
                missing.append(spec['backend_ref'])
        source_path = Path(ready['source_file']) if ready.get('source_file') else None
        source_hash = (hashlib.sha256(source_path.read_bytes()).hexdigest()
                       if source_path and source_path.is_file() else None)
        evidence_at = ((related or {}).get('observed_at')
                       or observation.get('observed_at'))
        try:
            age_seconds = max(0, (datetime.fromisoformat(observed_at) -
                datetime.fromisoformat(evidence_at)).total_seconds())
        except (TypeError, ValueError):
            age_seconds = None
        # Channel/target discovery cannot attest an operation-specific write
        # approval. The existing handler must validate its exact request.
        write_ready = (False if not permitted or spec['permission'] == 'read' else
                       None if name in CATALOG or name.startswith('db.') else True)
        metadata = {
            'observed_at': observed_at,
            'source_sha256': source_hash,
            'source_file': ready.get('source_file'),
            'backend': {'reference': spec['backend_ref'],
                        'binding_available': bool(binding_available),
                        'connection_verified': False,
                        'connection_status': 'NOT_PROBED' if binding_available else 'UNAVAILABLE',
                        'related_evidence': related,
                        'related_evidence_is_binding': False},
            'authority_ready': bool(permitted), 'acl_ready': bool(permitted),
            'read_authorized': bool(permitted and spec['permission'] == 'read'),
            'write_authorized': write_ready,
            'operation_approval': 'CALL_TIME_VALIDATION_REQUIRED' if write_ready is None else
                                  'NOT_GRANTED' if not write_ready else 'SCOPED_BINDING_VERIFIED',
            'readiness': 'CALL_TIME_VALIDATION_REQUIRED' if callable_now else 'UNAVAILABLE',
            'reason': reason, 'missing_fields': missing,
            'evidence_source': [spec['source_ref'], 'ToolRegistry.discover(current context)',
                                observation.get('owner_evidence_ref', str(evidence_path))],
            'backend_evidence_observed_at': evidence_at,
            'backend_evidence_age_seconds': age_seconds,
            'backend_evidence_ttl_seconds': None,
            'backend_evidence_freshness': 'OBSERVATION_ONLY_NO_RUNTIME_LEASE',
            'source_qualification': 'SOURCE_HASH_ONLY_EXISTING_CALL_TIME_GUARDS_REQUIRED',
            'evidence_is_authorization': False,
        }
        isolated = observation.get('isolated_tool_evidence', {}).get(name)
        # Independent dimensions: schema coverage and fixture evidence never
        # imply a live handler result. Deferred tools do not gate other rows.
        metadata['status_flags'] = {
            'REGISTERED': True,
            'EXPOSED': name in advertised,
            'METADATA_COMPLETE': bool(spec['capability'] and spec['source_ref']
                                      and spec['backend_ref'] and source_hash
                                      and inputs.get(name)),
            'TESTED_ISOLATED': bool(isolated and isolated.get('evidence_ref')),
            'LIVE_VERIFIED': False,
            'DEFERRED': reason is not None,
            'FAILED': False,
            'UNKNOWN': True,
        }
        metadata['isolated_test_evidence'] = isolated
        metadata['execution_result'] = 'UNKNOWN_NO_CURRENT_KMCW_LIVE_CALL_ATTESTATION'
        metadata['status_scope'] = 'CURRENT_CONTEXT; isolated tests are historical evidence only'
        rows.append({**spec, 'input_schema': inputs.get(name, spec['input_schema']),
                     **metadata, 'registered': True, 'advertised': name in advertised,
                     'available': bool(ready['available']), 'authorized': bool(permitted),
                     'callable': bool(callable_now), 'blocked_reason': reason,
                     'policy_decision': 'CALL_TIME_VALIDATION_REQUIRED' if callable_now else 'DENY'})
    return {'registered_count': len(rows), 'exposed_count': len(schemas),
            'callable_count': sum(row['callable'] for row in rows), 'tools': rows}


def serve(ctx, source=None, sink=None, *, context_provider=None):
    source = source or sys.stdin.buffer
    sink = sink or sys.stdout
    registry = ctx.get("tool_registry") or build_default_registry()
    initialized = False
    while True:
        raw = source.readline(MAX_MESSAGE + 1)
        if not raw:
            return 0
        if len(raw) > MAX_MESSAGE:
            print("KMCW_MCP_MESSAGE_TOO_LARGE: transport closed", file=sys.stderr)
            return 1
        request = None
        try:
            request = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
        else:
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                    or not isinstance(request.get("method"), str)
                    or ("id" in request and type(request["id"]) not in (str, int))):
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32600, "message": "Invalid Request"}}
            elif "id" not in request:
                continue
            else:
                response = {"jsonrpc": "2.0", "id": request["id"]}
                method = request["method"]
                params = request.get("params", {})
                if not isinstance(params, dict):
                    response["error"] = {"code": -32602, "message": "Expected object params"}
                elif method == "initialize":
                    version = params.get("protocolVersion")
                    response["result"] = {
                        "protocolVersion": version if version in VERSIONS else "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "kmcw", "version": "0.1.0-internal"}}
                    initialized = True
                elif not initialized:
                    response["error"] = {"code": -32000, "message": "Initialize first"}
                elif method == "ping":
                    response["result"] = {}
                elif method == "tools/list":
                    schemas = _schemas(registry, ctx)
                    catalog = _catalog(registry, ctx, schemas)
                    by_name = {row['name']: row for row in catalog['tools']}
                    for item in schemas:
                        item['_meta']['kmcw/capability'] = by_name[item['name']]
                    response["result"] = {"tools": schemas, "_meta": {
                        "kmcw/capabilities": catalog}}
                elif method == "tools/call":
                    try:
                        # Reacquire the issuer's short-lived authority for each
                        # call. Never reuse an expired grant or renew it locally.
                        from .capabilities import bind_runtime_context
                        call_ctx = bind_runtime_context(ctx, context_provider)
                        result = _call(registry, params, call_ctx)
                        response["result"] = {"content": [{
                            "type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                            "isError": False}
                        if params.get('name', '').startswith('kmcp.'):
                            response['result']['structuredContent'] = result
                    except (ToolError, ValueError, OSError) as exc:
                        reason = exc.reason if isinstance(exc, ToolError) else type(exc).__name__
                        print(f"KMCW_MCP_CALL_FAILED: {reason}", file=sys.stderr)
                        response["result"] = {"content": [{
                            "type": "text", "text": json.dumps({"ok": False, "reason": reason,
                                "detail": exc.detail if isinstance(exc, ToolError) else ""})}],
                            "isError": True}
                else:
                    response["error"] = {"code": -32601, "message": "Method not found"}
        sink.write(json.dumps(response, ensure_ascii=False) + "\n")
        sink.flush()


def main():
    """Launch the existing transport with its original runtime context gates."""
    import argparse
    from .km_main import _ctx
    from .capabilities import KMAdminContextProducer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--admin-runtime-context', action='store_true',
                        help='Use the existing protected Admin credential provider')
    args = parser.parse_args()
    ctx = _ctx(args)
    provider = None
    if args.admin_runtime_context:
        provider = KMAdminContextProducer.runtime_context
    return serve(ctx, context_provider=provider)


if __name__ == '__main__':
    raise SystemExit(main())

