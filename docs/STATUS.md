# สถานะโปรเจกต์ (อัปเดตล่าสุด: 2026-10-29)

สรุปสั้นสำหรับ "กลับมาหลังห่างหายไปนาน" — รายละเอียดเชิงลึกอยู่ที่
`docs/BACKLOG.md` (งานค้าง/ทิศทาง), `docs/RELEASE_PLAN.md` (แผนรุ่นถัดไป),
`AGENT.md` (กฎ + บทเรียนสำหรับ AI agent)

## ช่วงเก็บเสถียรภาพ 30 วัน

- **นับอย่างเป็นทางการตั้งแต่ 2026-09-28 — ครบ ~26-27 ต.ค. 2026**
- v1.25.10 คือรุ่นสุดท้ายที่ tag ได้ระหว่างหน้าต่าง (hotfix จริงยกเว้น —
  gate ใน release.yml เป็น `::warning::` ไม่บล็อก)
- หลักฐานวันเริ่ม: Stability cron run แรก success (run 36359400642)
  + `stability_report -Hours 24` ALL GREEN

## การเฝ้าระบบ (ทำงานเองอัตโนมัติ)

| ชั้น | ความถี่ | ผลลัพธ์ |
|---|---|---|
| Stability workflow (GitHub) `stability.yml` | รายวัน 04:00 ไทย | pytest + เช็ค release ล่าสุด — พัง = เปิด issue dedupe |
| Task "SSDTempMonitor Weekly Stability" (เครื่องนี้) | ทุกจันทร์ 10:55 | append สรุปที่ `%LOCALAPPDATA%\SSDTempMonitor\weekly_stability_summary.txt` |
| แอป + watchdog บนเครื่อง | ต่อเนื่อง | event log / watchdog.log — อ่านเองได้จาก docs/FAQ |

## สุขภาพล่าสุด (2026-10-29)

- cron Stability เขียวต่อเนื่องทุกวันพังตั้งแต่ 17 ต.ค. + dependabot รอบ 2
  (4 PR) แล้ว rerun-พิสูจน์ว่า installer-test ตอน merge #8 เป็น infra flake
  ไม่ใช่ regression — issue #16, #17 ปิดแล้วเพราะ stale
- **watchdog spawn storm แก้แล้ว (วันนี้)**: pythonw ที่รัน ssd_temp_tray.py
  หลอก "app not running" ให้ watchdog spawn exe ทุกนาที 28-29 ต.ค. แล้ว exe
  ตัวใหม่ชน mutex ออกเงียบ; แก้ watchdog.ps1 ให้ถือว่า run-from-source เป็น
  "app รันอยู่" + อัปเดต copy ใน install dir แล้ว — log เงียบตั้งแต่ 09:26 ปลุก
- บั๊ก secondary ของ `stability_report.ps1` (format `{1:s}` บน double)
  แก้แล้ว — ตอนนี้ report ชี้ finding จริงได้ถูกต้อง
- **บทเรียน 6 วันที่พังแล้วแก้ (2026-10-01 → 07)**: cron Stability failed
  เพราะ fixture ของ `TestWeeklyReport._rows()` ฝังวันที่ hard-coded
  (2026-09-17..23) เทียบกับ cutoff `today-7d` — พอข้าม 1 ต.ค. ข้อมูลจอ
  fixture ทั้งหมดตกนอกหน้าต่าง → `build_weekly_report_html` ว่าง → 2 เทสพัง
  ใน CI ทุกวัน แก้แล้ว fixture คำนวณวันแบบ NOW-relative → 401 passed บนเครื่อง
  (พิสูจน์ workflow เขียวอีกชั้นด้วย dispatch หลัง push — ปิด 6 issue `[stability]`
  ด้วยข้อความ root cause)
- **บทเรียน 2: transient blank ของ 24h CSV** — แอปเขียนทับทั้งไฟล์ด้วย
  `open(w)` ครั้ง/นาที มีหน้าต่าง ms ที่ไฟล์ว่าง (เจอจริง 15:25 รอบแรก).
  `stability_report` เพิ่ม retry 1 s ก่อน FAIL เมื่อไฟล์ว่าง (จะพิสูจน์อีกครั้งรอบถัดไป)
- startup baseline ยังน้อย: v1.25.10 n=5, avg 236 ms, p95 262 ms
  (ต้อง n≥30 ต่อรุ่น — ประเมินว่า n≈30 (~ 3-4 วัน) ก่อนวันครบหน้าต่าง 26-27 ต.ค.)

## dependabot

- รอบแรกปิดครบ 5 PR (2026-09-28): setup-python v7, gh-release v3,
  upload-artifact v7, github-script v9, deploy-pages v5 — CI เขียวทุก job
- รอบถัดไปคาด ~ปลาย ต.ค. (ตารางรายเดือน)
- หมายเหตุ: gh-release v3 ยังไม่ได้วิ่งจริง (ใช้ตอน push tag) —
  จะพิสูจน์ตอน release ถัดไป

## รุ่นถัดไป: v1.25.11 (เป้าหมายสัปดาห์แรก พ.ย. 2026)

- แผน + pre-flight checklist + checklist วันปล่อย: `docs/RELEASE_PLAN.md`
- เงื่อนไขหลัก: stability 7 วันเขียวหลังหน้าต่างครบ, startup_trend n≥30
  ต่อรุ่น, cron success, pytest เขียว, dependabot ค้าง 0

## ประวัติรุ่นล่าสุด

| รุ่น | วันที่ | หมายเหตุ |
|---|---|---|
| v1.25.10 | 2026-09-27 | stability gate non-blocking + telemetry รายสัปดาห์ |
| v1.25.9 | 2026-09-27 | FAQ อ่าน event log เอง |
| v1.25.8 | 2026-09-27 | จุดเริ่มช่วงเก็บเสถียรภาพ (1.25.2→1.25.9 ในวันเดียว) |

## สถานะ release (หน้าต่างครบแล้ว 29 ต.ค. — ปล่อยสัปดาห์แรก พ.ย. ตามแผน)

- pre-flight ล่าสุด (วันนี้): cron success + ไม่มี issue ใหม่ ✅ / dependabot
  ค้าง 0 ✅ / **pytest 415** ✅ (+7 จากเมื่อเช้า: hard-kill 4 + wiring 3) /
  startup n=5 ⏳ (ยังไม่ถึง 30 — เครื่องนี้
  รีสตาร์ต app ไม่บ่อย) / stability_report -Hours 168 = 2 finding ที่
  รู้สาเหตุและหมดอายุตามหน้าต่าง: (1) mutex_suspect 26 ต.ค. — app แก้แล้ว
  ไม่เขียน event ชนิดนี้กับ source-run holder อีก (~2 พ.ย. จึงออกนอกหน้าต่าง)
  (2) history gap วันนี้ 09:40 — transient การเขียนรายนาทีขาดหายชั่ววูบ
  แล้วกลับมาเองในรอบถัดไป; และ watchdog cadence 28-29 ต.ค. จัดเป็น `warn`
  แล้ว ไม่ตัด exit code
- issue ที่ cron เปิดค้าง: #16, #17 ปิดหมดแล้ว (สรุปตอนนี้ข้อค้าง = 0)
- ตามแผน RELEASE_PLAN: **วันปล่อยเป้าหมายสัปดาห์แรก พ.ย.** (ห่างจากปลาย
  หน้าต่าง 2-3 วัน) — อย่า tag v1.25.11 ก่อน 168 ชม. เขียว + startup n เพิ่ม

## งานล่าสุด (2026-10-29 ช่วงบ่าย)

- **poll hang ปิดแล้วครบ**: `_run_powershell` เปลี่ยนเป็น `Popen` + เมื่อหมด
  เวลา hard-kill ทั้ง process tree ด้วย `taskkill /F /T` — เพราะ CPython
  issue #87512 (`run(timeout=)` บน Windows ยัง drain pipe รอบสองหลัง kill
  ถ้า grandchild ค้างเปิด pipe ไว้ จะบล็อกต่อได้หลายนาที) เทสใหม่
  `TestRunPowershellHardTimeout` วัดกับโปรเซสแขวนจริง: จบ < 15 วิ เสมอ
- **tools/manual_selftest_watchdog_mutex.ps1 (ใหม่)** — สคริปต์ให้ผู้ใช้รัน
  ทดสอบเองบนเครื่องตัวเองเวลา troubleshooting: 7 ซีนครบทุก path —
  S1 fresh start (log INFO startup) / S2 duplicate ออกเงียบ exit 2 /
  S3 จับ source-run holder ได้ / S4 ผู้ถือแปลกปลอม → log mutex_suspect
  ที่ระบุชื่อ holder ก่อนออก exit 2 / S5 watchdog ไม่เขียน log เมื่อมี
  instance มีชีวิต / S6 quit marker → "skip: quit marker" / S7 ว่างจริง →
  spawn stub exe ทุกซีนทำงานใน isolated env (mutex/data dir/stub/plain)
  **พิสูจน์แล้ว: 7/7 PASS จริงวันนี้ (รันจาก elevated PowerShell)**
- watchdog.ps1 เพิ่ม hook 2 จุดรองรับ self-test: (1) `SSD_TEMP_WD_EXE` =
  ชี้ stub exe (และปิด src-probe เมื่อใช้ stub) (2) เมื่อ
  `SSD_TEMP_WD_DATA_DIR` ถูก set: หา watchdog_skip.flag **เฉพาะแค่ dir นั้น**
  — แก้บั๊กเดิมที่ isolated ซีนไปชน marker ของ %APPDATA% เครื่องจริงแล้ว
  skip ซะงัน
- pytest: **415 passed** (+7 ตัวใหม่: hard-kill 4 + wiring manual script 3),
  `py_compile` ผ่าน, PS-parser ทั้ง watchdog.ps1 และ manual selftest ผ่าน

## สถานะเดิม (2026-10-07)

- python -m pytest = 401 passed แล้วตั้งแต่วันนั้น (ปัจจุบัน 415)
