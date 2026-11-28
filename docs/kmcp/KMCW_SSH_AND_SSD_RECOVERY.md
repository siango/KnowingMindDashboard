# KMCW ผ่าน SSH และชุดสำรองย้าย Ubuntu SSD

Mission: ใช้ KMCW/KMCP แพ็กเกจเดิม คุย LLM แยกผ่าน terminal รัน graph ที่อนุมัติชัดเจน บันทึกหลักฐาน และกู้คืนสำเนาได้

## คำสั่งหลังติดตั้งผ่านการทดสอบเครื่องจริง

```bash
export PATH="$HOME/.local/bin:$PATH"
kmcw chat --config "$HOME/.config/knowingmind/kmcw-ssh.json"
```

คุยต่อด้วย session เดิมได้ พิมพ์ `/exit` เพื่อออก ข้อความเป็นการปรึกษา ไม่ใช่การอนุมัติหรือคำสั่ง shell จาก LLM

```bash
kmcw ask --config "$HOME/.config/knowingmind/kmcw-ssh.json" \
  --session ssh-main --request-id owner-question-001 \
  --text 'วิเคราะห์ข้อเท็จจริง สมมติฐาน และงานถัดไป'

export KMCW_RUNTIME_CONFIG="$HOME/.config/knowingmind/kmcw-owner-runtime.json"
km status
km tools
```

`request-id` เดิมกับข้อความเดิมอ่านผลที่บันทึกไว้ ไม่เรียกโมเดลซ้ำ หากการเรียกครั้งก่อนขาดกลางทางจะหยุดให้ตรวจสอบ ไม่ replay แบบไม่รู้ผล

## รันวงจรตรงผ่านผู้ใช้ Linux

```bash
kmcw circuit --config "$KMCW_RUNTIME_CONFIG" \
  --graph "$HOME/.config/knowingmind/recovery-canary.graph.json" \
  --command 'ตรวจหลักฐาน P01, Workers AI และ core memory' \
  --request-id owner-circuit-001
```

คำสั่งข้างบนแสดง preview เท่านั้น เมื่อจะรัน graph ที่ตรวจแล้ว ให้เพิ่ม `--approve` ใช้ตัวตน POSIX UID จริงและ config สิทธิ์ 0600 ผ่าน ToolRegistry, QueueStore, approval readback และ receipt เดิม ไม่อ้างเป็น KMAA session และไม่อนุญาต Canon/database tools หรือ destructive tools ด้วยช่องทางนี้

Graph อนุมัติผูก source SHA, runtime hash, registry contract, mission/task, node และ argument จริง การเปลี่ยน source หรือ graph ใต้ request เดิมจะถูกปฏิเสธ การครบ graph เป็น functional acceptance ไม่ใช่ independent QC

## KVES projection

```bash
kmcw graph --config "$KMCW_RUNTIME_CONFIG"
kmcw graph --config "$KMCW_RUNTIME_CONFIG" --circuit-id CIRCUIT_ID \
  --output "$HOME/circuit-observation.json"
```

เป็น backend projection ของ registry และ trace จริงสำหรับ KVES บน Admin เดิม ยังไม่ใช่หลักฐานว่า UI ของ KVES deploy แล้ว ไม่มีกราฟตัวอย่างถูกอ้างเป็น runtime จริง

## สำรองและตรวจคืน

Workflow `kmcw-ssh-recovery` สร้าง wheel และ driver บน GitHub แล้วส่งให้ runner Ubuntu เดิม ตรวจ hash ก่อนติดตั้ง เก็บสำรองบนเครื่องเท่านั้น:

- `~/KnowingMind/backups/kmcw-*.tar.gz`: git bundle, tracked-change patch หากมี, runtime ที่เลือก, catalog, offline wheels และ pg_dump ของ `km_microops`
- `~/KnowingMind/state/installations/kmcw-native-recovery.json`: ผลแต่ละขั้นและตำแหน่งสำรอง
- ไฟล์สำรองและ transcript สิทธิ์ 0600; โฟลเดอร์สำรอง 0700
- ไม่ส่ง archive, SQL dump, transcript หรือ credential ไปเป็น GitHub artifact
- ไม่รวม GitHub runner registration, SSH keys, Cloudflare OAuth/tunnel secret, DB password หรือ Admin cookie ต้องลงทะเบียน/เข้าสู่ระบบใหม่ที่เครื่องปลายทาง
- สถานะไฟล์ runtime เป็น snapshot ที่ตรวจ hash ไม่ใช่ global transaction ทั้งเครื่อง ควรพักงานก่อนทำ cutover; ห้ามเริ่ม worker สำเนาสองเครื่องกับ lease เดียวกัน

ตัวอย่างสำรองด้วยตัวเอง โดยใช้ runtime และ catalog ที่ยืนยันแล้ว:

```bash
install -d -m 700 "$HOME/KnowingMind/backups"
kmcw backup --source "$HOME/kmcp-host-setup" \
  --runtime EXISTING_RUNTIME --catalog EXISTING_CATALOG \
  --pg-service kmco_runtime --output "$HOME/KnowingMind/backups"
```

## ย้ายไป Ubuntu Desktop บน SSD

1. ติดตั้ง Ubuntu และ mount SSD ที่เลือกเองก่อน ห้ามใช้คำสั่ง format จากการเดาชื่อ disk; NTFS/Windows เดิมไม่ถูกแก้ใน workflow นี้
2. ทำสำเนา archive และ receipt ไปสื่ออีกลูก ตรวจ SHA-256 เทียบ receipt ไฟล์บน USB เดิมยังไม่ใช่ off-device backup
3. บน Ubuntu ใหม่ ใช้ Python 3.11+ และ venv ติดตั้ง wheel เดิมจากชุดสำรอง พร้อม psycopg wheels ที่ตรง OS/Python/CPU ชุดที่สร้างบน runner นี้ใช้ Ubuntu x64/Python 3.12
4. `kmcw restore --archive ARCHIVE --destination NEW_EMPTY_DESTINATION` ตรวจทุก payload ก่อนคืน ไม่ทับ directory เดิม และไม่ restore ฐานข้อมูลอัตโนมัติ
5. Clone `NEW_EMPTY_DESTINATION/recovery/source.bundle` เป็น workspace และ checkout SHA จาก manifest ใช้ binary patch หาก manifest ระบุ tracked changes
6. ติดตั้ง wheel จาก `runtime/state/recovery/wheels` ลง venv ใหม่ ปรับ workspace/runtime/catalog paths, node ID และ `posix:<UID>` เป็นของเครื่องใหม่ใน config ส่วนตัว อย่าสำเนา runner identity เดิม
7. สร้าง libpq service ที่ชี้ฐานข้อมูลเป้าหมายที่ตรวจแล้ว Restore `recovery/km_microops.dump` เฉพาะเป้าหมายที่ยืนยันและมีสำรองก่อน ไม่ใช้ `--clean` กับฐานข้อมูลที่กำลังทำงาน
8. ลงทะเบียน GitHub runner ใหม่, login Cloudflare ใหม่, ผูก tunnel และ Admin credential ผ่านช่องทางเดิม ตรวจ authenticated HTTP, lease/recovery และ canary ก่อนเปิดรับงาน

การทดสอบอัตโนมัติกู้ git bundle และ offline package ลง directory แยก และคืน pg_dump เข้า PostgreSQL cluster ชั่วคราวที่ใช้ private Unix socket เท่านั้น เทียบ hash ของ core record กับต้นทาง แล้วหยุดและลบ cluster ทดสอบ ไม่เปลี่ยนฐานข้อมูล production

## ขอบเขตที่ต้องมีหลักฐานแยก

Owner Node: Ubuntu runner รับผิดชอบติดตั้ง/ทดสอบ/สำรอง; GitHub รับผิดชอบ source/build evidence; KMAA รับผิดชอบสิทธิ์ remote command; KMNW รับผิดชอบ dispatch/lease; KVES รับผิดชอบ visual editor บน Admin เดิม

ไม่ถือว่า SSH canary พิสูจน์ KMAA/HTTP cutover, KMNW network dispatch, KVES UI, Cloudflare Containers, Dhamma Canon health หรือการบูตจาก SSD สำเร็จ แต่ละส่วนต้องมีผลทดสอบและ readback ของตัวเอง
