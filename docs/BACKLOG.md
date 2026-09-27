# Backlog

ที่มา: ทบทวนเมื่อ 2026-09-27 (หลังวันปล่อย 1.25.2→1.25.9) — โค้ดสแกน
TODO/FIXME แล้ว**ไม่มีค้าง** รายการนี้คือทิศทางที่คุยกันใน conversation
และโอกาสที่เห็นจาก telemetry ใหม่ จัดตามค่าที่คาดหวัง / แรงในการทำ

## P0 — ควรทำก่อน (รากฐานที่ทำให้ที่เหลือวัดได้)

- **เก็บเสถียรภาพ 1.25.9 ให้ครบ 30 วัน** — รัน
  `tools\stability_report.ps1 -Hours 24` ทุกสัปดาห์ (หรือให้ Stability
  workflow รายวันเป็นเสียงเตือน) เกณฑ์ปัจจุบัน ALL GREEN ตั้งแต่ 16:51
  ของวันที่ 27 — ห้าม tag รุ่นใหม่ทับก่อนครบกำหนด เว้นแต่มีบั๊กจริง
- **เก็บ startup_ms 1–2 สัปดาห์แล้ววิเคราะห์** — `python
  tools\startup_trend.py --days 14` (เพิ่มวันนี้) ตัวอย่างปัจจุบัน n=2
  (377, 236 ms) ยังสรุปไม่ได้ พอ n≥30 ต่อรุ่นค่อยตัดสินว่า cold-start
  ถดถอยหรือไม่ และใช้เป็น baseline กัน regression ในอนาคต

## P1 — มูลค่าชัด ทำได้ในรอบสั้น

- ~~คู่มือ "อ่าน event log เอง" ใน FAQ~~ ✅ เสร็จ (v1.25.9: ตาราง 9 keys
  + watchdog.log + stability_report พร้อม test เฝ้า)
- ~~stability gate ใน release.yml~~ ✅ เสร็จ (v1.25.10: non-blocking —
  อายุ release ก่อนหน้า < 7 วัน → ::warning:: hotfix ไม่ถูกบล็อก)
- **dependabot รอบแรก** — พรุ่งนี้ขึ้นไปจะมี PR อัปเดต action versions
  รีวิวและ merge ให้ CI ยังเขียว (ตารางรายเดือน ไม่รีบ)

## P2 — โอกาส/ความสวยงาม

- **กราฟ startup_ms ในหน้า Details แบบ canvas** — ตอนนี้เป็น ASCII
  sparkline พอใช้ ถ้าอยากได้เส้นจริงค่อยทำ (แรงพอสมควร ผลตอบแทนต่ำ)
- ~~telemetry สรุปรายสัปดาห์~~ ✅ เสร็จ (v1.25.10: section ใน weekly
  report นับ update_check/fetch_retry/fetch_failed/update_backoff 7 วัน)
- **stability_report รู้จัก `startup ms=`** — เพิ่มเกณฑ์ "startup กลาง ๆ
  หลุด p95 เกิน 3 เท่า = เตือน" เมื่อข้อมูลเริ่มเยอะ

## Parked — ตัดสินใจภายหลัง

- **Code signing** (docs/CODE_SIGNING.md) — ผู้ใช้ขอพักไว้ก่อน:
  ต้องซื้อ/จัดการใบรับรองและตั้ง secrets ทำเมื่อพร้อมเรื่องต้นทุน
  ก่อนหน้านั้น installer ยัง unsigned (SmartScreen เตือนครั้งแรก
  ซึ่งผ่านได้ด้วย "More info → Run anyway")
- **เผยแพร่/ประชาสัมพันธ์** — รอเสถียรภาพ 30 วันผ่านก่อนจะชวนคนใช้
