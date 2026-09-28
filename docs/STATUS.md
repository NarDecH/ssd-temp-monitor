# สถานะโปรเจกต์ (อัปเดตล่าสุด: 2026-09-28)

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

## สุขภาพล่าสุด (2026-09-28)

- stability report 18-24 ชม.: ALL GREEN (mutex_suspect รายการสุดท้ายเป็น
  ของทีม debug 27 ก.ย. 16:09-16:15 — ไม่ใช่อาการแอป)
- update_check 21 ครั้ง/วัน ปกติ (fetch_failed = 403/DNS ภายนอก)
- startup baseline ของ v1.25.10: n=5, avg 236 ms, p95 262 ms
  (ตั้งเกณฑ์เตือนถดถอยที่ >3× p95 แล้วใน stability_report — warn-only)

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
