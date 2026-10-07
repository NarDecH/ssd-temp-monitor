# สถานะโปรเจกต์ (อัปเดตล่าสุด: 2026-10-07)

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

## สุขภาพล่าสุด (2026-10-07)

- stability report 24 ชม. + 168 ชม.: ALL GREEN (event log / watchdog / update
  check สม่ำเสมอกว่า 8 ครั้ง/6 ชม. / 24h history 1440 แถวไม่มี gap > 5 นาที)
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

## สถานะ release (วันที่ 10 ของหน้าต่าง 30 วัน — 2026-10-07)

- pre-flight ของ v1.25.11 ยัง **ไม่ครบ** — ข้อที่พร้อม: pytest 401 ✅;
  startup_trend n=5 ⏳ (ต้อง ≥30 ต่อรุ่น, คาดถึงวันครบหน้าต่าง 26-27 ต.ค.)
- cron Stability เขียวแล้วหลังแก้ 3 root cause (9c tiếp → 48c223f / f861d91 /
  5be530b) — รอบอัตโนมัติถัดไป 04:00 พรุ่งนี้ต้องยังเขียว
- dependabot บัลลาเงิล = 0 (รอบ 2: 4/4 merge วันนี้) ✅
- ตามแผนปล่อย **สัปดาห์แรก พ.ย.** — ไม่มี hotfix ให้คิดเลยวันนี้
