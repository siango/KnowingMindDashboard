<!-- KMCP-RUNTIME-CHECKPOINT-20261123 -->

## Runtime checkpoint — 23 November 2026 (Asia/Bangkok)

Status: IN_PROGRESS / NOT_WHOLE_SYSTEM_ACCEPTED. This append updates the historical installation section without promoting design or CI to production truth. The owner has since authorized implementation and real deployment; older read-only design restrictions are historical and do not override that instruction. Retired KCC/SG2 paths are not revived by this update.

| Component | Observed evidence | Remaining acceptance |
|---|---|---|
| Ubuntu GitHub runner | ubuntu-usb online, native jobs run as siango UID1002 | SSD boot and off-device recovery not proven |
| KMCW SSH LLM | Real provider chat and durable session dedup passed in run 45030098028 | Native P01 → Workers AI → core recall failed; original checkpoint diagnosis required; database backup/restore stages not reached |
| KMCP source and package | Local-services software SHA 455355c25c6eadf683564971435d7f8923d9aa33: 628 Python tests (2 existing skips), 4 Node custom-node tests; build passed | These are source/package checks, not protected public 18856 acceptance |
| n8n / ArtCraft | Official pinned assets verified and preserved as draft release; build job 137311620172 passed | Actual host job 137312163252 in run 45056121975 is IN_PROGRESS at this observation; no local install or PNG success claimed |
| Cloudflare | Native Workers AI inference passed; account inventory readback succeeded | Containers empty at inventory; DNS API 403; D1/R2/Queues metadata does not establish full write/readback/restore circuits |
| Media Studio | PR291 SHA bca0d34cbea249b291dd742da999e1312d26a933 rejects incomplete/corrupt PNG, limits decompression and checks supplied text metadata; 11 tests passed locally and CI run45056949198 | Canon independent attestation, final PNG↔SVG equivalence, brand geometry in final image, visual QC and publication NOT_VERIFIED |
| KVES / KMNW / KMAA | Existing design/backlog retained; KVES native registry/trace projection source available | Complete deployed UI/runtime product acceptance remains unverified |
| KnowingMindDhamma | Connector repeatedly requests authentication retry, even after acceptance | No successful live project/Canon readback or SSOT update confirmed; do not infer PostgreSQL outage |
| Facebook / LINE | Existing callback/worker retained; Messenger draft PR267 source only | Actual Page-token association, webhook signatures/subscription and production receive/reply circuit not proven; no test messages/posts sent |

Code and evidence references:
- https://github.com/siango/KnowingMindDashboard/pull/6
- https://github.com/siango/KnowingMindDashboard/pull/7
- https://github.com/siango/KnowingMindDashboard/pull/8 (read-only native failure diagnosis; no circuit replay/inference)
- https://github.com/siango/KnowingMindDashboard/actions/runs/45056121975
- https://github.com/siango/KnowingMindApp/pull/291
- https://github.com/siango/KnowingMindApp/actions/runs/45056949198

Next: let the serialized native installer finish; read its actual receipt, then diagnose old checkpoint/schema without replaying ambiguous outcomes. Fix the evidenced first blocker, execute original scoped circuit, validate database write/readback and isolated backup restore, then qualify public auth and service/channel integration. Windows NVMe is not an authorized inferred Linux target: do not format it. No whole-system image labelled complete or uninterrupted-service guarantee is justified by current evidence.

Source-contract correction: the SSH recovery canary referenced p01#/output/state without declaring state in its result schema. The original graph is reproducibly rejected before execution with CIRCUIT_OUTPUT_PORT_UNDECLARED; the corrected graph declares that typed port. Two regression tests pass locally. This is a proven source defect, not yet an independently established explanation of the old host failure; native replay has not occurred. Native diagnostics become opened-only to prevent unrelated documentation commits from enqueueing repeat host reads.

The owner has explicitly added functional Trader integration on KMAA to this continuing mission. Preserve the existing paper/shadow/read-only scope until specific live-order authority is supplied; no claim of real provider feed or account connectivity without live readback.
