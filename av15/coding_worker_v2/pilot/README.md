# KMCW Commercial V1: internal single-project pilot

**Not released for external distribution or sale.** No new SaaS, IDE, billing,
queue, memory database, signer or control plane. Installation is free under the
proposed model; a paid KMCW right within the GitHub Copilot ecosystem still needs
confirmed terms, channel, price, licensing and actual payment evidence.

## Installed scope

Linux, Python 3.12+, Bash, Git and OpenSSL. One trusted OS account, one authorized
Git repository, existing tracked Python files, allowlisted unittest, isolated
worktree, no push/merge/deploy. Tests execute repository code with the caller's
OS permissions: the existing command/env policy is **not an OS/network sandbox**.
Never install against untrusted repositories or a shared-user runtime.
`account_id` is an explicit local binding, **not authenticated multi-tenancy**.
Use a separate OS identity/runtime for another customer's data.

The existing ToolRegistry exposes six bounded MCP tools; execution still uses
`execute_plan`, QueueStore, receipt history and the original memory. The original
Mission Auditor's global scope and signed external verifier remain mandatory;
they are not replaced with a pilot-shaped PASS. Without their authorized
configuration, coding acceptance is BLOCKED before dispatch or at verification.
There is no automatic signing service in this kit.

## Install / configure / remove

From the existing source's `av15/` directory:

```bash
./km pilot install \
  --workspace /absolute/path/to/authorized-test-repo \
  --runtime-base /absolute/path/to/pilot-runtime \
  --account-id pilot-local --worker-id AV15 \
  --allowed-path calc.py \
  --context-path calc.py --context-path test_calc.py \
  --test-command "python3 -m unittest" \
  --provider copilot-cli --authorize-subscription \
  --auditor-root /absolute/path/to/existing-pinned-auditor \
  --authority-snapshot /absolute/path/to/authorized-snapshot.json \
  --verifier-public-key /absolute/path/to/verifier-public.pem
```

Only a public key path is configured. Never give the worker a signing key.
`config.example.json` has no credentials; the installer writes source hashes
and absolute local paths to `.kmcw/config.json`. Reinstall after source changes.
Do not commit generated local paths or runtime state. No secrets, Copilot
credentials, subscriptions or binaries are copied into the package.

The installer adds only its own `kmcw` entry to project `.mcp.json` and installs
`.github/agents/kmcw.agent.md`. It refuses an existing installation, modified
files, symlinked targets, or shadowing an existing `.github/mcp.json`. Existing
unrelated MCP entries are preserved. Run Copilot from the authorized repository
with the customer's own authenticated account:

```bash
copilot --agent kmcw
```

Trust the project and approve only the KMCW tools according to local policy.
For unattended prompt-mode evaluation after that approval:

```bash
copilot --agent kmcw --trust-folder --allow-tool=kmcw \
  --usage-output-file /absolute/path/to/evidence/copilot-usage.json \
  -p "Fix addition in calc.py; test it and read back the verified receipt."
```

Other MCP servers from user/plugins remain subject to Copilot configuration;
disable unrelated ones for an isolated measurement. Do not use blanket
`--allow-all-tools`. No KMCW tool may mint a proof or approve a host prompt.

The agent searches lessons, loads approved context, saves a stable request ID,
runs the plan and reads the receipt/account-work binding. A second task must
set `after_task_id` to the first task and cannot run until its verified,
conflict-free receipt is present. On a new process reuse the original request/
plan ID. `pilot_read` recovers the saved report; `pilot_run` delegates actual
lease/checkpoint recovery and reuse to the existing executor. Uncertain provider
dispatches are never blindly retried.

Readback without another model call:

```bash
./km pilot call pilot_read --config /repo/.kmcw/config.json \
  --args '{"plan_id":"pilot-REPLACE_WITH_SAVED_ID"}'
./km pilot uninstall --workspace /absolute/path/to/authorized-test-repo
```

Uninstall checks installed hashes, removes only owned files/server registration,
preserves other MCP entries and **retains all runtime/receipts/worktrees**.
Stop clients before uninstall; no shared process is killed. If an install was
interrupted, inspect `.kmcw/install.json` before any manual cleanup. No broad
recursive removal is used.

## Memory and measurement

`baseline` keeps original receipt retrieval. `--memory-ranking qc` opts into
derived tags and QC-outcome scoring; neither changes receipts nor establishes
current verification. Conflicting receipts and other accounts are excluded.
Four-part lessons retain immutable original receipt references/hashes. CAUSE
remains unresolved unless supported; tags are lexical, not semantic embeddings.
pgvector integration is **BLOCKED** until an authorized adapter to the existing
memory is identified; no parallel database or fabricated vector test is added.

```bash
./km pilot benchmark --workspace /repo --runtime-base /runtime \
  --account-id pilot-local --cases /evidence/labeled-cases.json \
  --corpus-kind historical-observation --rounds 10
```

Cases are `[{"goal":"arithmetic","relevant_task_ids":["actual-task-id"]}]`.
Only independent labels support relevance claims; unlabeled observations
measure latency only. Reports separate baseline/QC retrieval p50/p95, hit@5,
receipt provenance, passing/nonpassing historical usage, and unknown values.
The benchmark invokes no model. Synthetic tests are not live recovery or QC.
`account_work` binds account/task/worker/output/receipt and records
`PAYMENT_UNVERIFIED`, with no asserted revenue.

Successful-generation evidence contains tokens/time/cost. Only qwen-local,
deepseek-local and deepseek-api tokens are provider-reported; echo word counts
and CLI adapters without parsed usage are reported as unknown (`token_basis`),
never summed as tokens. Missing,
failed or interrupted-provider usage stays unknown; it is not zero or a billed
price estimate. Copilot orchestration usage must be collected separately with
`--usage-output-file`; do not divide tokens by zero live QC passes. A controlled
Copilot-alone comparison and verified paying customer are still required before
an investment or superiority claim.

## Internal package and license/dependency check

```bash
./km pilot package --output /absolute/path/to/kmcw-internal.zip
```

The explicit archive allowlist includes only worker Python modules, the `km`
launcher and custom-agent template, with hashes in `DISTRIBUTION.json`.
No env, queue, keys, receipts, vendor code, node_modules, model, Copilot binary
or unrelated repository is shipped. Extract into a fresh directory, `chmod +x
km`, then use the same commands. Python dependencies are stdlib only. Git,
Bash, OpenSSL, Copilot and optional Ollama are customer-installed prerequisites,
not bundled or sublicensed. Their applicable terms and customer entitlements
must be checked by the distributor; no third-party licensing is inferred.

The source repository currently has **no declared distribution license**.
Archive creation is internal-only and marks external distribution BLOCKED.
Do not send it to customers until source ownership/license, dependency terms
and Copilot ecosystem commercial terms are confirmed.

## Acceptance boundaries

Local tests cover transport, bounded tools, account isolation, idempotency,
missing-authority fail-closed behavior, dependency blocking, clean no-pip venv
archive installation, and restart/readback. Existing simulated executor tests
exercise checkpoint reuse but are not an authorized external verification.

Live acceptance requires two actual fixes, passing original QC and signed proof,
receipt-linked lesson consumption in task two, fresh-process recovery, and
measured usage. Missing snapshot/key/proof means BLOCKED, never sale-ready.
KMNW work can use only its existing real queue and fresh authorized node
registry; stale READY projections, offline nodes, KCC or retired nodes cannot
be used as capacity. No new network dispatcher is supplied by this kit.
