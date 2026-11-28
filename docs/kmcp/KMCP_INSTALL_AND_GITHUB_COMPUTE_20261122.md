# KMCP — อัปเดต วิธีติดตั้ง และ GitHub Compute

บันทึกวันที่ 22 พฤศจิกายน 2026 (Asia/Bangkok)
สถานะ: SOURCE_AND_CI_VERIFIED / INSTALLABLE_PACKAGE / PRODUCTION_INTEGRATION_PENDING

## 1. บริบทและขอบเขต

KMCP เป็นชื่อเป้าหมาย MCP V5 ตามเอกสาร KEBUNATE Core/SoS ล่าสุด ใช้ต่อกับ Mission KMCP-V5-PRODUCTION เดิม ไม่สร้าง Mission, queue, scheduler หรือ SSOT ใหม่ บันทึกนี้อ้าง source ของ KMCW PR #1 และ KMCP PR #6; G0-G1 เป็นบริบท checkpoint ของ source เดิม ไม่ใช่การเปลี่ยน Mission production ไปอีกงาน

GitHub เป็น source และ CI evidence; Google Drive เป็นเอกสารอ้างอิงการออกแบบ/ติดตั้ง ไม่ใช่ production execution truth การใช้งานจริงต้องบันทึกสถานะและหลักฐานผ่าน authority/ledger เดิม

## 2. สิ่งที่พัฒนาแล้วและหลักฐาน

- PR: https://github.com/siango/KnowingMindDashboard/pull/6 — OPEN, READY FOR REVIEW, ยังไม่ merged ณ การอ่านล่าสุด
- Tested source head: f1fc26d147766c038269316ba69f4e080b793e53
- Tested PR merge SHA: e5f990f04f26ed8f8dde1c3adde14e46a468d659
- CI run 151: https://github.com/siango/KnowingMindDashboard/actions/runs/45001511664 — SUCCESS
- Ubuntu/Python 3.11, Ubuntu/Python 3.12 และ macOS/Python 3.12 ผ่านครบ แต่ละชุดรัน 574 tests: PASS 572, SKIP เดิม 2
- มี circuit tests ใหม่ 32 กรณี รวม stdio command → submit → run → status, dataflow ผ่าน code_search/KMCO P01, การบันทึก work_units เข้า KMCW เดิม, restart/dedup, port types, final acceptance, permission revocation และ receipts
- ทุก OS build wheel และทดสอบติดตั้งใน venv แยกสำเร็จ มี wheel, logs และ source-receipt.json ใน Artifacts ของ CI
- ทดสอบโปรแกรมจริงในสภาพแวดล้อมแยกและใช้ fixtures ที่ระบุชัด ไม่ใช่หลักฐานว่างาน production หรือระบบภายนอกผ่านแล้ว

เพิ่ม tools แบบ opt-in 6 ตัว: kmcp.command, kmcp.circuit.submit, kmcp.circuit.run, kmcp.circuit.status, kmcp.circuit.cancel, kmcp.circuit.report

ฐาน default 67 tools; เปิด circuits = 73; เปิด KMCO catalog ที่ตรวจ integrity ด้วย = 78 ไม่ใช่จำนวน tools ที่รับรองว่า live บน public endpoint

## 3. GitHub มี compute จริงอะไรบ้าง

| ประเภท | ใช้ทำอะไร | สถานะ/ข้อจำกัด |
|---|---|---|
| GitHub-hosted Actions runner | เครื่อง VM ชั่วคราวสำหรับรัน Python/Node/tests/build และงานซอฟต์แวร์แบบมีจุดจบ | ใช้กับ KMCP แล้ว; ไม่ต้องเปิด AV15 |
| GitHub Codespaces | เครื่องพัฒนาแบบ interactive เปิด terminal/IDE และทดลองแอป | ยังไม่ได้เปิดให้โครงการนี้ในงานรอบนี้; มี timeout/usage billing |
| Self-hosted runner | GitHub ส่งงานให้เครื่องของเรา | ขึ้นกับเครื่องนั้นเปิดอยู่; ไม่ใช่ compute ที่ GitHub จัดเครื่องให้ |
| Runtime เดิมบน Cloudflare/โฮสต์ที่ได้รับอนุญาต | บริการต่อเนื่องและ authority/backend จริง | แยกจาก CI; ต้องมี live acceptance |

เอกสาร GitHub ระบุ standard ubuntu-latest สำหรับ public repository เป็น 4 CPU, RAM 16 GB, SSD 14 GB; เป็น VM ใหม่ต่อ job และ standard public runners ใช้งานฟรีตามนโยบายและข้อจำกัดที่ระบุ ไม่รวมค่า paid runner/model/API/storage อื่น ๆ หรือรับรองยอดโควตาบัญชีผู้ใช้

Job ของ GitHub-hosted runner มีเพดาน 6 ชั่วโมง แต่ workflow KMCP นี้กำหนด timeout-minutes: 15 เอง ข้อมูลบนดิสก์ runner ไม่ใช่ฐานข้อมูลถาวร ต้องนำผลที่ต้องเก็บออกเป็น artifacts/แหล่งจัดเก็บเดิมก่อน job จบ

Codespaces ใช้พัฒนาได้ แต่มี idle timeout (ค่าเริ่มต้น 30 นาที) และอายุการทำงานสูงสุด 12 ชั่วโมงต่อ session ตามเอกสารปัจจุบัน จึงไม่ควรนับเป็น runtime production 24/7

Runner สามารถรัน KMCW/KMCO และ PostgreSQL/service containers สำหรับ integration tests ได้เมื่อเตรียม dependency และสิทธิ์ของ test environment ไว้ การรัน workflow ไม่ได้สร้าง production authority โดยอัตโนมัติ

เอกสารอ้างอิงที่ตรวจวันที่ 22 พ.ย. 2026:
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- https://docs.github.com/en/actions/reference/limits
- https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle

## 4. ติดตั้งจาก GitHub โดยไม่ดาวน์โหลด/โยนไฟล์ด้วยมือ

Supported: Python 3.11+ บน Linux/macOS; Windows ใช้ WSL2 หรือ Linux container ยังไม่รับรอง native Windows เพราะ QueueStore ใช้ POSIX locks

สร้าง venv สำหรับ package แล้วติดตั้ง source ที่ผ่าน CI แบบ pin commit:

```bash
python3 -m venv .venv-kmcp
. .venv-kmcp/bin/activate
python -m pip install 'git+https://github.com/siango/KnowingMindDashboard.git@f1fc26d147766c038269316ba69f4e080b793e53#subdirectory=av15'
kmcp --help
```

คำสั่งข้างต้นติดตั้ง package/CLI เท่านั้น ไม่เปิด public port ไม่ย้าย SSOT และไม่เปลี่ยนสิทธิ์ production ใช้ package wheel จาก CI run 151 เป็นอีกทางเลือกได้

วาง runtime configuration ไว้นอก source โดยใช้ workspace และ runtime_base ของระบบเดิมที่มีอยู่:

```json
{
  "schema": "KMCP_RUNTIME_V1",
  "node_id": "worker-01",
  "account_id": "OWNER_ACCOUNT_FROM_EXISTING_SYSTEM",
  "mission_id": "KMCP-V5-PRODUCTION",
  "task_id": "EXISTING_CANONICAL_TASK_ID",
  "workspace": "../project",
  "runtime_base": "../existing-runtime",
  "poll_tools": ["pilot_reconcile"]
}
```

node/account/mission/task ต้องเป็นค่าจริงจากทะเบียนเดิม ตัวพิมพ์ใหญ่ข้างต้นเป็น placeholder ไม่ใช่ ID ที่สร้างหรืออนุมัติแล้ว Paths แบบ relative อ้างจากตำแหน่งไฟล์ config ไม่ใช่ cwd ถ้าต่อ coding lane ให้เพิ่ม pilot_config ของการติดตั้งเดิม; ถ้าใช้ KMCO ให้เพิ่ม kmco_catalog ของ catalog ที่ตรวจ hash แล้ว ห้ามใส่ token, cookie, private key หรือ auth claims ใน JSON นี้

ตรวจ catalog:

```bash
kmcp --config /absolute/path/runtime.json catalog
```

เปิด local MCP stdio ตาม runtime authority ที่ได้รับอนุญาต:

```bash
kmcp --config /absolute/path/runtime.json serve --admin-runtime-context
```

สำคัญ: Admin context producer และ public HTTP adapter เดิมยังเป็น READ-ONLY การเปิดด้วย flag นี้ไม่ทำให้สั่งเขียนได้เอง สำหรับ execution ต้องเชื่อม approved write delegation ใน server integration ตามสัญญาเดิม แล้วให้สิทธิ์ run และ child tools ตาม manifest การติดตั้ง package สำเร็จกับสิทธิ์สั่งงานเป็นคนละเรื่อง

API integration และสัญญา typed graph:
https://github.com/siango/KnowingMindDashboard/blob/f1fc26d147766c038269316ba69f4e080b793e53/av15/coding_worker_v2/KMCP_PORTABLE_CIRCUITS.md

## 5. วิธีสั่งงานและเกณฑ์พร้อมใช้จริง

1. Host AI รับคำสั่งแล้วส่ง typed graph หรือ KMCW work_units ไป kmcp.command; ผู้เรียก command-only ใช้ planner ที่ตั้งค่าไว้ ไม่อ้างว่ามีโมเดลถ้ายังไม่มี
2. ตรวจ manifest: source SHA, installed runtime hash, tool contracts, input/output ports, approved scope และ final acceptance
3. Submit ไป checkpoint/queue lane เดิม ยึด ID เดิมและ idempotency; ขออนุมัติ manifest ตาม authority เดิม
4. Run เป็น bounded ticks; output ของขั้นก่อนเข้าสู่ขั้นถัดไปผ่าน refs; อ่าน status/report ได้
5. KMCW recipe ใช้ pilot_submit → pilot_reconcile → pilot_read; KMCO ใช้ registered handlers จริง
6. ตรวจ final artifact, functional acceptance และ independent QC; เก็บ receipt และ live readback ผ่าน ledger เดิม

COMPLETED ของ circuit หมายถึงขั้นงานและ final output contract ผ่าน ไม่ใช่ independent production QC; receipt ยังระบุ QC_PENDING จน verifier เดิมรับรอง การ cancel หยุดระหว่างขั้น ไม่ได้รับรอง kill process ที่กำลังรัน และการทำงานรุ่นนี้เป็น sequential

ถ้า process หายระหว่าง mutation และผลยังไม่ทราบ ระบบ BLOCKED_OUTCOME_UNKNOWN แทนการทำซ้ำ ต้อง reconcile หลักฐานเดิมก่อน ไม่สร้าง task ใหม่เพื่อข้าม blocker

## 6. พัฒนาและทดสอบตอน AV15 ปิด

สิ่งที่ทำได้ทันที: แก้ source บน branch/PR เดิม, รัน CI tests, เริ่ม service ทดสอบแยกเมื่อ workflow รองรับ, ทดสอบ MCP protocol ด้วย fixture authority, build/install package และเก็บ reports/artifacts ทั้งหมดใช้ compute ของ GitHub

สิ่งที่ยังไม่เกิดจาก CI: production deployment, สิทธิ์เรียก public writes, การเข้าถึงฐานข้อมูลส่วนตัวที่ไม่เชื่อม network/auth และการรับรองงานลูกค้าจริง

Workflow เดิมรองรับ push/pull_request และประกาศ workflow_dispatch; UI/manual dispatch ต้องมี workflow พร้อมใช้งานตามเงื่อนไข default branch ของ GitHub ก่อน จึงไม่อ้างว่าปุ่ม Run workflow เปิดใช้แล้วจากการมี YAML ใน PR อย่างเดียว ตอนนี้ run 151 เป็นหลักฐานการรันจริงผ่าน pull_request อยู่แล้ว ไม่ต้องรันซ้ำเพื่อพิสูจน์ว่ามี compute

แนวทางที่ใช้ต่อ: GitHub = source/build/test/bounded software jobs; Cloudflare/runtime เดิม = continuous production service; Drive = design/install/evidence references ใช้ pipeline และ authority เดิม ไม่ตั้ง runner/SSOT ใหม่ซ้ำ

## 7. สถานะถัดไป

SOURCE/CI: VERIFIED สำหรับ code SHA ข้างต้น
PACKAGE: BUILT_AND_INSTALL_TESTED
PR: READY_FOR_REVIEW / NOT_MERGED
PUBLIC_WRITE: NOT_YET_INTEGRATED
LIVE_END_TO_END + INDEPENDENT_QC: NOT_VERIFIED_BY_THIS_UPDATE

Next Action: รักษาลำดับ dependency ของ PR #1/#6; เชื่อม write delegation ของ runtime เดิม แล้วรับงานจริงหนึ่งงานจนได้ artifact + independent QC + receipt ระหว่างนั้นงาน source, tests และ package ทำต่อบน GitHub ได้ทันที ไม่ต้องรอ AV15
