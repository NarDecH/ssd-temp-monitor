# Backlog

> สถานะรวมฉบับย่อ: `docs/STATUS.md` · แผนรุ่นถัดไป: `docs/RELEASE_PLAN.md`

ที่มา: ทบทวนเมื่อ 2026-09-27, สถานะล่าสุด 2026-09-28 (หลังวันปล่อย
1.25.2→1.25.10) — โค้ดสแกน
TODO/FIXME แล้ว**ไม่มีค้าง** รายการนี้คือทิศทางที่คุยกันใน conversation
และโอกาสที่เห็นจาก telemetry ใหม่ จัดตามค่าที่คาดหวัง / แรงในการทำ

## P0 — ควรทำก่อน (รากฐานที่ทำให้ที่เหลือวัดได้)

- **เก็บเสถียรภาพ 1.25.9 ให้ครบ 30 วัน** — **นับอย่างเป็นทางการแล้ว
  (เริ่ม 2026-09-28, ครบ ~26-27 ต.ค.; v1.25.10 คือรุ่นสุดท้ายของ
  หน้าต่างนี้)** — หลักฐานวันเริ่ม: Stability workflow cron run แรก
  สำเร็จ (run 36359400642, 2026-09-27 23:38 UTC, job daily-check
  success ไม่มี issue ใหม่) และ `stability_report -Hours 24` ALL GREEN
  (event เดียวในหน้าต่างคือ mutex_suspect ของทีม debug เมื่อ 27 ก.ย.
  16:09-16:15 ซึ่งหลุดหน้าต่างเองหลัง ~16:15 ของวันที่ 28) —
  ห้าม tag รุ่นใหม่ทับก่อนครบกำหนด เว้นแต่มีบั๊กจริง (แผนรุ่นถัดไป +
  checklist วันปล่อย: `docs/RELEASE_PLAN.md`) — ตั้งแต่ 2026-09-28
  task "SSDTempMonitor Weekly Stability" รัน `tools\stability_report.ps1`
  ให้ทุกจันทร์ 10:55 สรุปสะสมที่
  `%LOCALAPPDATA%\SSDTempMonitor\weekly_stability_summary.txt`
  (นอกจากนี้ Stability workflow รายวันยังเป็นเสียงเตือนฝั่ง GitHub)
- **เก็บ startup_ms 1–2 สัปดาห์แล้ววิเคราะห์** — `python
  tools\startup_trend.py --days 14` (เพิ่มวันนี้) ตัวอย่างปัจจุบัน n=2
  (377, 236 ms) ยังสรุปไม่ได้ พอ n≥30 ต่อรุ่นค่อยตัดสินว่า cold-start
  ถดถอยหรือไม่ และใช้เป็น baseline กัน regression ในอนาคต

## P1 — มูลค่าชัด ทำได้ในรอบสั้น

- ~~คู่มือ "อ่าน event log เอง" ใน FAQ~~ ✅ เสร็จ (v1.25.9: ตาราง 9 keys
  + watchdog.log + stability_report พร้อม test เฝ้า)
- ~~stability gate ใน release.yml~~ ✅ เสร็จ (v1.25.10: non-blocking —
  อายุ release ก่อนหน้า < 7 วัน → ::warning:: hotfix ไม่ถูกบล็อก)
- ~~dependabot รอบแรก~~ ✅ เสร็จ (2026-09-28: merge squash ครบ 5 PR —
  setup-python v7, gh-release v3, upload-artifact v7, github-script v9,
  deploy-pages v5 — patch เป็น bump pin เท่านั้น CI บน main เขียวทุก job
  รวม watchdog-e2e)

## P2 — โอกาส/ความสวยงาม

- **กราฟ startup_ms ในหน้า Details แบบ canvas** — ตอนนี้เป็น ASCII
  sparkline พอใช้ ถ้าอยากได้เส้นจริงค่อยทำ (แรงพอสมควร ผลตอบแทนต่ำ)
- ~~telemetry สรุปรายสัปดาห์~~ ✅ เสร็จ (v1.25.10: section ใน weekly
  report นับ update_check/fetch_retry/fetch_failed/update_backoff 7 วัน)
- ~~stability_report รู้จัก `startup ms=`~~ ✅ เสร็จ (2026-09-28: เกณฑ์ที่ 5
  — latest เทียบ 3×p95 ของตัวอย่างก่อนหน้า = **warn-only** ไม่มีผล exit
  code, มีเทสครอบสัญญา 400 passed)

## Parked — ตัดสินใจภายหลัง

- **Code signing** (docs/CODE_SIGNING.md) — ผู้ใช้ขอพักไว้ก่อน:
  ต้องซื้อ/จัดการใบรับรองและตั้ง secrets ทำเมื่อพร้อมเรื่องต้นทุน
  ก่อนหน้านั้น installer ยัง unsigned (SmartScreen เตือนครั้งแรก
  ซึ่งผ่านได้ด้วย "More info → Run anyway")
- **เผยแพร่/ประชาสัมพันธ์** — รอเสถียรภาพ 30 วันผ่านก่อนจะชวนคนใช้
