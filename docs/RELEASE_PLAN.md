# แผนปล่อยรุ่นถัดไป (หลังหน้าต่างเสถียรภาพ 30 วัน)

> สถานะหน้าต่าง: **นับอย่างเป็นทางการตั้งแต่ 2026-09-28** (cron run แรกของ
> Stability workflow สำเร็จ + `stability_report` ALL GREEN) — ครบกำหนด
> **~26-27 ต.ค. 2026** v1.25.10 คือรุ่นสุดท้ายที่ tag ได้ระหว่างหน้าต่าง
> (ยกเว้นบั๊กจริง — hotfix ไม่ถูกบล็อก มีแค่ `::warning::` จาก stability
> gate ใน release.yml)

## เป้าหมายรุ่นถัดไป: v1.25.11 (หรือ v1.26.0 ถ้ามี feature ใหญ่)

วันปล่อยเป้าหมาย: **สัปดาห์แรกของ พ.ย. 2026** (ให้ห่างจากวันครบหน้าต่าง
2-3 วัน เพื่อดู report 7 วันหลังปลายหน้าต่างก่อนตัดสินใจ)

### เงื่อนไขก่อนปล่อย (pre-flight)

- [ ] `tools\stability_report.ps1 -Hours 168` → **ALL GREEN** ทั้ง 7 วัน
      (ดูสรุปสะสมได้ที่ `%LOCALAPPDATA%\SSDTempMonitor\weekly_stability_summary.txt`
      เขียนอัตโนมัติทุกจันทร์ 10:55 โดย task "SSDTempMonitor Weekly Stability")
- [ ] `python tools\startup_trend.py --days 30 --json` → มีอย่างน้อย
      n≥30 ต่อรุ่น และ avg/p95 ของรุ่นใหม่ไม่ถดถอยจาก v1.25.10
      (baseline ปัจจุบัน: n=5, avg 236 ms, p95 262 ms)
- [ ] Stability workflow cron ล่าสุด = success และไม่มี issue ใหม่เปิดโดย workflow
- [ ] `python -m pytest tests/ -q` เขียวบนเครื่อง (baseline: 397 passed)
- [ ] dependabot PR ค้าง = 0 (รอบถัดไปคาด ~ปลาย ต.ค.)

### candidate features (เลือกใส่ตามแรงที่มี — จาก BACKLOG)

| ข้อ | แหล่ง | เวอร์ชันที่เหมาะ | หมายเหตุ |
|---|---|---|---|
| startup_trend วิเคราะห์ + ตั้ง baseline ต่อรุ่น | P0 | v1.25.11 | รอข้อมูล n≥30 ก่อน (ประมาณกลาง ต.ค.) |
| stability_report เกณฑ์ `startup ms=` (p95 เกิน 3 เท่า = เตือน) | P2 | v1.25.11 | ทำคู่กับข้อบน ใช้ข้อมูลเดียวกัน |
| กราฟ startup_ms canvas ในหน้า Details | P2 | v1.26.0 | แรงพอสมควร ผลตอบแทนต่ำ — ทำเมื่อมีเวลาเหลือจริง |
| เผยแพร่/ประชาสัมพันธ์ | parked | — | ทำหลังปล่อยรุ่นถัดไปเสร็จ (ปล่อยก่อนแล้วค่อยชวนคนใช้) |

### checklist วันปล่อย (จำลำดับจาก AGENT.md)

1. bump `APP_VERSION` ให้ครบ 4 จุด: `ssd_temp_tray.py`, `setup.iss`,
   README badge, docs/README.html badge
2. เพิ่ม section ใหม่ใน `docs/CHANGELOG.md` + `.html` (release.yml อ่าน
   section ตาม tag มาเป็น release body — ถ้าลืม gate จะเตือน)
3. `python -m pytest tests/ -q` + `python -m py_compile ssd_temp_tray.py`
4. commit (ไม่ต้องรอ — วันปล่อยคือหลังหน้าต่างครบแล้ว) → tag `vX.Y.Z` →
   push พร้อม tag
5. รอ CI 3/3 เขียว + release.yml สร้าง release 5 assets + SHA256SUMS
   (stability gate ควรเงียบ เพราะ release ก่อนหน้าอายุเกิน 7 วันแล้ว)
6. **self-update เครื่องนี้ทันที** (ตาม pattern ใน AGENT.md —
   `request_elevation` ตั้งแต่ launch รอบแรกเลย เพราะ Inno เช็ค AppMutex
   ตอน setup เริ่ม) → ตรวจ `_update_result.txt`:
   `version=X procs=N install_exit=0 hash=match`
7. ตรวจ `startup version=X ms=...` ใน event log + stability_report
   -Hours 6 วันรุ่งขึ้น
8. ปรับ BACKLOG (ปิดข้อที่ทำ ✅, เลื่อนข้อที่ไม่ได้ใส่)

### ถ้าเกิดบั๊กจริงระหว่างหน้าต่าง

- ปล่อย hotfix ได้ (ห้ามปล่อยทิ้งไว้เพราะกลัวหน้าต่าง) — gate เป็น
  `::warning::` ไม่บล็อก
- หลัง hotfix **นับหน้าต่างใหม่** จากวันปล่อย hotfix (ตามนโยบายเดิม
  ที่ทำกับ 1.25.2→1.25.9)
