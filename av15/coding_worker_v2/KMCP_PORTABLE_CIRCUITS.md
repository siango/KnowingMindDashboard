# KMCP portable command-to-circuit execution

Continue the existing KMCW/MCP V5 Mission. This source adds six opt-in tools to
the ORIGINAL registry, uses its QueueStore checkpoints/events and KMCW versioned
receipts, and invokes every child through the original MCP authorization.
It creates no scheduler, worker service, database or authority issuer.

## User flow

1. The owner gives the MCP host one goal. The host AI decomposes it into typed
   `graph` nodes, or into KMCW `work_units`, and calls `kmcp.command`.
   Command-only callers can use an operator-injected `kmcp_planner` callable or
   the existing Qwen provider. Missing inference returns a named blocker, never
   an invented plan. Host-supplied decomposition needs no local model or API.
2. Compilation validates tools, dependencies, declared output ports, edge
   types and final acceptance. It returns an immutable manifest and digest.
3. `kmcp.circuit.submit` stores that plan in the existing checkpoint store.
   Account/mission/request ID deduplicates; different content conflicts.
4. Approve the manifest through the existing task-scoped authority. Invoke
   `kmcp.circuit.run` with its ID/digest. It executes a bounded tick; call again
   for remaining steps. Completed steps are reused after process restart.
5. `status`/`report` show node results, hashes, progress, blockers, final outputs,
   acceptance checks and the existing receipt reference. `cancel` requests a
   stop between steps; active child execution retains its own stop policy.

`COMPLETED` means all declared steps and final output contracts passed.
Independent QC is NOT self-issued. The versioned KMCW circuit receipt stays
`QC_PENDING` until the existing verifier handles it. Tool execution, functional
acceptance, independent verification and deployment are separate dimensions.

## Six tools

| Tool | Inputs | Result |
|---|---|---|
| `kmcp.command` | request_id, command; graph OR work_units optional | manifest, digest, validated node order |
| `kmcp.circuit.submit` | manifest | circuit_id, digest, dedup status |
| `kmcp.circuit.run` | circuit_id, manifest_sha256, max_steps optional | complete per-node report |
| `kmcp.circuit.status` | circuit_id | current report |
| `kmcp.circuit.cancel` | circuit_id | cancellation request acknowledgment |
| `kmcp.circuit.report` | circuit_id | outputs, acceptance, receipt and next action |

Default registry remains 67. `build_default_registry(circuits=True)` has 73.
With the integrity-checked KMCO catalog it has 78 (67 + 5 + 6).
PhotoCraft remains a separate optional adapter.

## KMCW and KMCO together

For coding goals, send the existing `decompose_plan` work-unit contract:

```json
{
  "request_id": "repair-calculator",
  "command": "Repair the calculator and verify delivery",
  "work_units": [
    {
      "task_id": "t1",
      "goal": "Implement the approved calculator fix",
      "deps": [],
      "allowed_paths": ["calc.py"],
      "acceptance": ["Existing calculator tests pass"],
      "input_contract": ["Pinned calculator source"],
      "output_contract": ["Corrected calculator patch"],
      "evidence_required": ["Tests and independent QC"]
    }
  ]
}
```

This compiles `pilot_submit -> pilot_reconcile -> pilot_read`, preserving
original task IDs, admission, source scopes and independent QC. `pilot_config`
must identify the EXISTING workspace/base/account/mission. The recipe never
opens a second queue. `pilot_reconcile` polling must be explicitly allowed by
operator configuration. Its `BLOCKED`/`UNAVAILABLE` is reported as a blocker;
missing authority is not silently retried. Intermediate `PARTIAL` remains
waiting until the existing supervisor reaches `DONE` or the poll budget ends.

General graphs can combine registered KMCW tools with KMCO tools. A data edge
uses `{"$ref":"dependency_id#/field"}`. Declare that node in `after` and declare
the field/type in the producing node's `result_schema`. Arrays use JSON Pointer
indices. References are data only; they never become executable expressions.

```json
{
  "nodes": [
    {
      "id": "proof",
      "tool": "kmco.p01.evidence",
      "arguments": {"file": "approved.txt", "quote": "Approved source line"},
      "after": [],
      "result_schema": {
        "type": "object",
        "required": ["output"],
        "properties": {"output": {"type": "object", "required": ["text"],
          "properties": {"text": {"type": "string"}}}}
      }
    }
  ],
  "outputs": {"answer": {"$ref": "proof#/output/text"}},
  "acceptance": [{"path": "/answer", "schema": {"type": "string", "enum": ["Approved source line"]}}]
}
```

Schema support is deliberately explicit: type, properties, required,
additionalProperties (boolean), items, enum, min/maxItems, min/maxLength.
Unsupported keywords and external references are rejected, not ignored.

## Host-neutral install

Supported execution environments: Python 3.11+ on Linux/macOS, and Windows
through WSL2 or a Linux container. Existing QueueStore uses POSIX file locks;
native Windows is not claimed tested. No AV15 hostname, home directory,
Cloudflare account or node ID is embedded in this portable layer.

```bash
python3 -m pip install ./av15
kmcp --config /path/to/runtime.json catalog
kmcp --config /path/to/runtime.json serve --admin-runtime-context
```

For an MCP client, launch `kmcp` with arguments `--config`, your runtime config
path, `serve`, and the appropriate existing context provider integration.
The installed launcher uses the existing newline-delimited MCP stdio transport.
No inbound port, SSH or tunnel is required for local MCP clients.

Configuration example: `pilot/kmcp-runtime.example.json`. Resolve paths relative
to the config file, not an operator's current directory. `node_id` is explicit;
WorkerConfig is injected into pilot loading, avoiding the old AV15 default.
Optional `kmco_catalog` points to an integrity-checked catalog, and
`pilot_config` points to the existing pilot installation.

Container option from repository root:

```bash
docker build -f av15/Dockerfile -t knowingmind-kmcp av15
docker run --rm -i -v /your/authorized/workspace:/workspace knowingmind-kmcp --config /workspace/runtime.json serve
```

The image contains Git/OpenSSL and the package. Add only language/toolchain
dependencies actually needed by the target project. Models and credentials
are runtime dependencies, not baked into the image.

## Authority integration (important implementation boundary)

JSON config accepts paths and identity labels, NEVER authentication claims,
tokens, approvals, signer keys or a Python producer import path.
The existing Admin producer is currently READ-ONLY; selecting it does not
magically authorize the new mutation tools. Production Admin write delegation
must be supplied through that deployment's approved authority lane.

An embedding server can construct the registry with `circuits=True` and call
the existing `mcp_server.serve(ctx, context_provider=trusted_provider)`.
The provider's established task-scoped auth/role/binding/approval contract must
explicitly grant `kmcp.circuit.run` AND permitted child tool names. Bind its
arguments to circuit_id/manifest_sha256 (and max_steps if supplied). The runner
attenuates this ONE parent approval into the exact calls resolved from the
approved manifest. It revalidates parent expiry/scope/source/approval for each
child and does NOT create `verified=True` child approval objects. Unlisted
child capabilities remain denied. DB/ingress/GUI retain additional native gates.

For deployments issuing fresh per-step context, trusted IN-PROCESS code may
set `ctx['kmcp_step_context'](base, tool_name, resolved_arguments)`. Its return
passes through the original `bind_runtime_context`; no tool input can install
this callable. This supports credential rotation without changing source.

The old public HTTP readback adapter remains read-only. This change ships a
portable stdio/API execution layer; it does not deploy or open a public HTTP
write gateway. No runtime identity or live production success is inferred.

## Failure/recovery semantics

- Reserve each node durably before dispatch. An interrupted RUNNING node is
  BLOCKED with `CIRCUIT_OUTCOME_UNKNOWN_DO_NOT_REPLAY`; never blind-replay it.
- Poll only read tools or operator-qualified `pilot_reconcile`; finite poll
  and per-tick step budgets. A failed/ambiguous mutation is never auto-retried.
- Persist successful outputs with hashes, and check result types BEFORE any
  dependent node runs. `rc != 0`, `ok=false` and `isError=true` are failures.
- Reject account/mission/node/source/contract drift, changed approved digest,
  recursive circuit dispatch, cycles, unknown tools/ports and constant fake
  final output. A local checkpoint hash is integrity detection, not a signature.
- Run ticks under the original checkpoint directory's nonblocking file lock.
  No distributed fencing is claimed from this local lock.
- This first implementation executes sequentially. Parallel multi-node leases,
  native process cancellation and arbitrary JSON Schema are not claimed.

## GitHub development and acceptance

The existing `coding-worker-v2` Actions workflow now runs the complete suite on
Ubuntu/Python 3.11 and 3.12 and macOS/Python 3.12, builds a wheel, tests the
INSTALLED entry point in a separate venv, and uploads wheel/test log/source-SHA
receipt artifacts. No AV15, model server, production DB or secret is needed.

New tests exercise real ToolRegistry + code_search + KMCO P01 dataflow, actual
KMCW work-unit submission, typed ports and aggregate acceptance, persistent
resume/dedup, ambiguous interruption, parent-approval attenuation/revocation,
cancel/poll budgets, original versioned receipts, MCP discovery and a future
node's relative-path config. Tests label fixtures as fixtures and never sign
production acceptance.

Source change -> GitHub CI -> downloadable tested wheel -> configure existing
authority/adapters on any supported future host. Host configuration and live
external-system acceptance happen when that host is available; development,
regression tests and packaging happen now in GitHub.
