# G0-G1 — Multi-host source reconciliation before SSD cutover

Status: INVENTORY_IMPLEMENTED / HOST_EVIDENCE_PENDING / CUTOVER_NOT_GRANTED.
This is a continuation of KnowingMindDashboard PR #6, not a new authority,
registry, queue, execution service, or second KEBUNATE mission.

## Known evidence and authority boundaries (2026-11-25 ICT)

| Environment | Evidence | Readiness |
|---|---|---|
| GitHub, KnowingMindDashboard | PR #6 on feature/kmcp-portable-circuits-20261122; exact SHA must be fetched when running the comparison | Source authority for KMCP/KMCW/KMCO/KARL scoped changes, not host runtime |
| GitHub, KnowingMindApp | PR #275 OPEN; Trader V2 docs/status LIVE_DISABLED; PR #276 CLOSED/superseded | Design/implementation source, not accepted live Binance |
| GitHub, kebunate | Existing Postgres and D1 contract/migrations | Different data-plane owner; do not replace |
| AV15 Ubuntu Live USB | /dev/sda, 28.7 GiB; /dev/sda2 ext3 casper-rw 16 GiB; preexisting km_microops and local services | Runtime evidence is historical; live exact SHA and DB backup still pending |
| Windows/WSL on AV15 | /dev/nvme0n1p3 NTFS, read-only mount; WSL ext4.vhdx about 46.79 GiB | VHDX identified; inner Git worktrees and DB state NOT INSPECTED |
| New Ubuntu SSD | Not detected in last supplied lsblk | INSTALLATION_NOT_STARTED |

Do not treat a GitHub branch as a full system backup. Do not treat a VHDX file
size as unique data quantity. Do not unregister a WSL distribution or overwrite
its VHDX based on this document.

## Existing repository source reconciliation

Use the new source inventory on each *running* Linux environment. It is local,
read-only and emits git metadata only, not file contents, secrets or database rows.
Adjust roots only to paths that actually exist.

```bash
python3 scripts/host/reconcile-source-inventory.py --node ubuntu-usb \
  --root "$HOME/kmcp-host-setup" --root "$HOME/KnowingMind/workspaces"
```

From a WSL session (after booting Windows; not while Ubuntu USB boots on the
same physical AV15):

```bash
python3 scripts/host/reconcile-source-inventory.py --node wsl \
  --root "$HOME/KnowingMind" --root "$HOME"
```

After the SSD has booted Ubuntu Full, use `--node ubuntu-ssd` and its actual
workspace roots. Explicit roots only; scans are bounded and ignore caches,
venvs, node_modules and state directories. A missing repo or source in the
reported roots is not proof it does not exist elsewhere. Worktree changes
require a separate owner-approved preservation step (git bundle, patch/untracked
archive and SHA-256 checks). Never push local credentials or secrets.

## KEEP / MERGE / RETIRE acceptance

1. For each repository, compare remote canonical identity, branch, current
   SHA, commit ancestry, tracked/untracked change counts. If histories diverge,
   retain both references and produce a change inventory; no force-push.
2. KEEP upstream verified commits from all sources. MERGE only reviewed changes
   missing from the canonical branch. Preserve private runtime configuration
   and database records outside Git.
3. Independently back up WSL and USB PostgreSQL (roles, grants, data, receipts)
   in a consistent fashion, and restore to an isolated target. Document exact
   schemas, row counts, and checksum/readback before selecting one writer.
4. Never run USB and SSD self-hosted runners with duplicate identity; never
   open production PostgreSQL writers from both boots.
5. RETIRE WSL only after the SSD restore and boot/authority/independent QC pass,
   a verified recovery backup exists, and the Windows owner approves deletion.
6. Production release stays NO-GO until installed-source SHA, local MCP
   authorized/unauthorized calls, PostgreSQL exact readback, external HTTP
   acceptance, independent QC and rollback prove current operation.

## Required reconciliation ledger (metadata, not secrets)

For each component record: mission, task, repository, branch, source SHA,
node, workspace, dirty count, database owner, runtime config location
(reference only), backup manifest/checksum, test run, installed SHA, receipt,
verifier, authority, current blocker and next action.

UNKNOWN must remain UNKNOWN. GitHub is canonical **source/version** SSOT;
PostgreSQL/D1 each retain their own authorized live-data SSOT. Neither is
replaced by markdown or a copied database.

## AV15 Ubuntu USB observed delta — 2026-11-25 03:55–03:58 ICT

Source: human-supplied AV15 terminal screenshots. These are OBSERVED host
outputs, not a GitHub Action receipt, independent QC, database backup, or a
live query made by this document. Source inventory was run from a script
extracted at pinned GitHub commit
`3032aa34bd9cbbb9e5727e70fad13aa37da65a02` without checking out over
the original USB working tree.

### Local inventory — seven detected workspaces

| Workspace under /home/siango | HEAD prefix | Tracked changes | Untracked files | Preservation |
|---|---|---:|---:|---|
| KnowingMind/workspaces/km-mission-auditor-source | e47b1433 | 0 | 0 | Clean by this scope |
| KnowingMind/workspaces/kmco-github-qc | b0692964 | 7 | 220 | REQUIRED |
| KnowingMind/workspaces/kmcp-pr6-delta | 6d9ebc6b | 0 | 0 | Clean by this scope |
| KnowingMind/workspaces/kmcw-git-recovery | e9cb91af | 22 | 346 | REQUIRED: git metadata/worktree relation |
| KnowingMind/workspaces/kmcw-recovery-staging/kmcw-pr1-head | e9cb91af | 22 | 346 | REQUIRED: actual source tree |
| KnowingMind/workspaces/kmcw-selftest-wurrwepn | dc9b69b7 | 0 | 1 | REQUIRED: classify untracked file |
| kmcp-host-setup | 6d9ebc6b | 0 | 0 | Clean by this scope |

Counts from the two KMCW paths MUST NOT be added as separate independent
changes. Git `rev-parse --show-toplevel` from the path
`kmcw-git-recovery` resolves to the nested
`kmcw-recovery-staging/kmcw-pr1-head` source tree. Both show the same
HEAD, tracked changes (22 files, +3392/-171 lines), and 346 untracked
paths. `kmcw-git-recovery` contains a `.git` directory but no `av15`
directory; `kmcw-pr1-head` has the actual `av15/coding_worker_v2`
tree. The earlier file-hash comparison of 0 versus 367 therefore used
the wrong filesystem root for the first side. It does NOT establish 367
different source files.

`kmco-github-qc`: 7 tracked files (+159/-5 lines), 220 untracked.
`kmcw-selftest-wurrwepn`: one untracked runtime-test JSON file, not
yet classified. The KMCW repository `origin` resolves to a historical
locally mounted Git bundle used during recovery, NOT directly to GitHub;
never assume `git push origin` updates the canonical repository.

### Reconciliation and backup stop conditions

- Preserve the KMCW gitdir and its nested worktree as an inseparable unit
  until core.worktree/gitdir linkage, existing changes and backup hashes are
  verified. Do not remove the apparently empty parent.
- Classify the 220 KMCO and 346 KMCW untracked entries into source, test,
  runtime state, cache, or secret *before* staging. Do not run `git add .`.
- Compare clean/preserved local commits and reviewed patches against the
  current PR #6 HEAD by content and history, not timestamps or file counts.
- The WSL 46.79 GiB ext4.vhdx has only been found on an NTFS **read-only**
  mount; the internal WSL git history and PostgreSQL have NOT been read.
- There is no verified off-device backup or SSD restore. No WSL deletion,
  USB cleanup of these source trees, force-push, production deployment, or
  parallel GitHub runner identity is authorized by this record.

### Mission handoff

Mission: G0-G1 original. Owner nodes: AV15 Ubuntu USB (host observations),
GitHub PR #6 (source ledger), WSL (pending inventory), SSD (future restore).
Task: reconcile all divergent source states. Input: bounded host inventories
above. Output: this evidence-bound central **source ledger entry**.
Status: SOURCE_LEDGER_UPDATED / LIVE_SSOT_NOT_MUTATED / BACKUP_PENDING /
CROSS_HOST_RECONCILIATION_PENDING / PRODUCTION_NO_GO.
Next action: verify KMCW gitdir/worktree relationship; classify and secure
local deltas, take authenticated backups, then reconcile WSL and GitHub by
exact SHA. This documentation commit is NOT an AV15 PostgreSQL/D1 write.
