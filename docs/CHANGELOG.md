# Changelog

รูปแบบตาม [Keep a Changelog](https://keepachangelog.com/th/1.1.0/)
เวอร์ชันตาม [SemVer](https://semver.org/lang/th/)

## [1.23.1] — 2026-09-24

### Fixed

- 🚑 **v1.23.0 แนะนำบั๊กใหม่: tray message loop รันสองเธรดพร้อมกัน** —
  watchdog ถูกเพิ่มมาโดยยังเรียก `App.run()` (ซึ่งรัน `icon.run()` อยู่แล้ว)
  ทำให้มี message pump สองตัวบน tray เดียว แอปจะเสถียรไม่ได้เลย — ตอนนี้
  watchdog เป็นเจ้าของ `icon.run()` ตัวเดียว (พร้อมสตาร์ท poll thread เอง)
  และมีเทสกัน regression นี้ตลอดไป

## [1.23.0] — 2026-09-24

### Fixed

- 🛑 **กด Settings แล้วโปรแกรมปิดตัว (v1.22.0 ยังเหลือ crash)** — เจอ crash
  ใหม่ใน Windows Event Log (tcl86t.dll 0x80000003) จากการเปิดหน้าต่าง tk
  สองบานพร้อมกัน (เช่น About ค้างไว้ + กด Settings): Tcl interpreter สองตัว
  ถูกใช้ข้ามเธรด และ `tk_after` รอบก่อนยังเรียก `event_generate` ข้ามเธรดอยู่
  — เขียน marshaler ใหม่ที่ **ไม่แตะ Tcl จากเธรดอื่นเลย** (เข้าคิวล้วน ๆ +
  drain loop บนเธรด tk เอง หยุดเองเมื่อหน้าต่างปิด) และเพิ่ม **watchdog**
  ที่รี-รัน tray message loop ถ้าถูก crash แบบ native ปิดทิ้ง (log
  `tray_loop_restarted`) — crash อีกก็ไม่ดับทั้งแอป
- 📴 **หน้า About ค้าง "Latest release: checking…"** — callback ที่หายไป
  พร้อม crash (จากปัญหาเดียวกัน) + ไม่มี retry เมื่อเน็ตล่มชั่วคราว —
  worker ตอนนี้ retry จนถึง deadline 20 วิ แล้วขึ้น "offline" ให้เสมอ
  (คีย์ใหม่ `about.latest.retrying` ครบ 4 ภาษา) และเช็ค "หน้าต่างยังอยู่ไหม"
  ด้วย flag แทนการเรียก `winfo_exists` ข้ามเธรด
- 🎨 **หน้า About อ่านไม่ออกบนธีมสว่าง** — สีข้อความเคย hardcode สำหรับธีม
  มืด (`#e2e8f0`, `#94a3b8`) ทำให้แทบมองไม่เห็น — ใช้พาเลตต์ธีมปัจจุบันทั้ง
  หน้า (เพิ่มสี `link` ในพาเลตต์) และไฮไลต์ผลเช็คเวอร์ชัน: **มีเวอร์ชันใหม่ =
  ส้มตัวหนา**, เวอร์ชันตรง = เขียว, offline = สี muted

### Changed

- Rotating log เปิดไฟล์แบบ lazy (delay=True) — startup ไม่ติดถ้าไฟล์ log
  ถูกล็อกโดยโปรแกรมอื่น
- ตอน quit ให้เวลาหน้าต่าง tk อื่น ๆ ปิดตัวก่อน 0.3 วิ ลดโอกาส Tcl panic
  ตอนปิดโปรแกรม

## [1.22.0] — 2026-09-24

### Added

- ♻️ **Reset statistics** — เมนู tray "ล้างสถิติ" และปุ่มใน Settings → Stats:
  ล้างบันทึกสุขภาพรายวัน ตัวนับแจ้งเตือน (overheat/SMART) ค่าพื้นฐาน SMART
  และประวัติ 24 ชม. ให้เป็นของใหม่ มีกล่องยืนยันก่อนลบ (พร้อมอธิบายขอบเขต)
  ไฟล์ที่ส่งออกไว้และกราฟย่อย (fine history) ไม่ถูกแตะ — ครบ 4 ภาษา

### Fixed

- 🛑 **โปรแกรมปิดเองโดยไม่มีสาเหตุ** — จุดราก 2 แห่ง: (1) คลิกเมนู
  "24-hour history"/"Usage stats" แล้ว `AttributeError` หลุดออกจาก message
  loop ของ pystray ทำให้ `icon.run()` คืนและโปรเซสจบแบบเงียบ ๆ (ตรงกับ crash
  ใน Windows Event Log) — ตอนนี้ callback ทุกตัวถูกกัน exception + `_spawn_once`
  ใช้ default เมื่อ flag ยังไม่มี (2) การเรียก `root.after` จาก worker thread
  (Settings/About) ไม่ thread-safe และเคยแตกใน tcl86t.dll (0x80000003) —
  เปลี่ยนเป็น marshal ผ่าน `<<SsdMarshal>>` บนเธรด tk เอง และ poll loop
  กัน crash ต่อรอบ (log `poll_error`) ไม่ให้ดับทั้งแอป
- 🕒 **กราฟ 24 ชม. ว่างหลังอัปเดตที่เครื่อง** — ไฟล์ `ssd_temp_history_24h.csv`
  เขียนเฉพาะตอน quit สำเร็จ (และ crash ทำให้ไม่เคยเขียน) ตอนนี้ persist ทันที
  ที่มีข้อมูลนาทีใหม่ และเติมย้อนหลังจาก fine history ตอนไฟล์ว่าง
- 📊 **ตัวนับแจ้งเตือนใน Stats นับเกิน** — `count_log_events` เคยนับ `smart_alert`
  รวมกับ `smart_alert_done` และ event อื่นที่มีชื่อต่อท้าย แก้เป็น match
  คำเต็ม (word boundary) แล้ว
- 🎨 **ข้อความ Stats จางบนธีมสว่าง** — หน้าต่าง/แท็บ Stats ตั้ง fg ตามพาเลตต์
  ธีมปัจจุบันเสมอ (เดิมพึ่ง default ของ tk)

### Changed

- หน้าต่าง Stats และกราฟ 24 ชม. จำตำแหน่ง/ขนาด (geometry) เหมือนหน้าต่างอื่น
- เพิ่มสไลด์แนะนำโปรเจกต์ 6 หน้า: `docs/slides.html` (ลิงก์จากหน้าดาวน์โหลด)

## [1.21.0] — 2026-09-23

### Added

- 🟩 **โซนสีอุณหภูมิในกราฟ** — พื้นหลังกราฟ (ทั้งเส้นปกติและ 24 ชม.) แบ่งเป็น
  แถบเขียว/ส้ม/แดงจาง ๆ ตามเกณฑ์เดียวกับสี tray (<51 / 51–64 / ≥65 °C)
  เห็นโซนที่อุณหภูมิอยู่ทันทีโดยไม่ต้องอ่านตัวเลข
- 🖼️ **ส่งออกกราฟ 24 ชม. เป็น PNG** — ปุ่มใหม่ในหน้าต่างกราฟ 24 ชม.
  (ใช้ Save As เลือกที่เซฟเหมือนกราฟปกติ)
- 📈 **เมนู "สถิติการใช้งาน" บน tray** — เปิดหน้าต่างสรุป 7 วันโดยตรง
  ไม่ต้องผ่าน Settings (ข้อมูลเดียวกับแท็บ Stats: จำนวนแจ้งเตือน +
  ต่อดิสก์ min/avg/max/wear) ปุ่มรีเฟรชในหน้าต่าง

### Verified

- 🌡️ โพรบก่อน build: source code ล่าสุดอ่าน SMART จริง (temp=36°C) แสดงผลครบ
  โซนสีที่ 36°C = เขียว ตรงกับเกณฑ์ tray และ Stats อ่าน event log จริงได้

## [1.20.0] — 2026-09-23

### Added

- 🕒 **กราฟย้อนหลัง 24 ชั่วโมง** — เมนู tray ใหม่: ดูอุณหภูมิย้อน 24 ชม.
  เต็ม (ข้อมูลรายนาที เก็บในไฟล์แยกของ DATA_DIR — portable ยกไปได้) เส้น
  ตารางเวลา -3h…-21h และสรุป min/avg/max รีเฟรชเองทุก 30 วินาที
- 🌗 **โหมดมืด/สว่างตาม Windows** — Settings → General เลือก
  `auto` (ตามธีมแอปของ Windows)/`dark`/`light`: หน้าต่างกราฟ หน้า Settings
  (พรีวิว) และแท็บ Stats ใช้พาเลตต์ถูกโหมดทันที (เดิมเป็นมืดตายตัว)
- 📊 **แท็บ Stats ใน Settings** — สรุป 7 วัน: จำนวนครั้งที่แจ้งเตือนอุณหภูมิเกิน
  และ SMART อ่านจาก event log จริง + ต่อดิสก์: ต่ำสุด/เฉลี่ย/สูงสุด/
  การสึก — มีปุ่มรีเฟรช แปลครบ 4 ภาษา

### Verified

- 🌡️ โพรบก่อน build: source code ล่าสุดอ่าน SMART จริง (temp=36°C) แสดงผลครบ
  และฟีเจอร์ใหม่ทั้งหมดทดสอบกับข้อมูลจริง (รวมการตรวจจับธีมสว่างของ Windows)

## [1.19.0] — 2026-09-23

### Added

- ⏰ **รายงานสุขภาพรายสัปดาห์อัตโนมัติ** — เปิดได้ใน Settings → General:
  ทุก 7 วันแอปจะสร้างรายงาน HTML ลงโฟลเดอร์ที่กำหนด (มีปุ่มเลือกโฟลเดอร์)
  พร้อมแจ้ง toast เมื่อบันทึก — ไม่ต้องกดเองอีกต่อไป
- 🗂️ **Export CSV เปิดใน Excel ได้ทันที** — ปุ่มใหม่ในแท็บ Health:
  ส่งออกข้อมูลรายวันทั้งหมดเป็น CSV แบบ UTF-8 BOM + `sep=,` + CRLF
  ซึ่ง Excel เปิดแล้วคอลัมน์ตรงทันที (ไม่ต้อง import)
- 🖥️ **กราฟเปรียบเทียบทุกดิสก์ในภาพเดียว** — เครื่องที่มี SSD ตั้งแต่ 2 ตัว
  ขึ้นไป รายงานรายสัปดาห์จะเพิ่มกราฟรวมอุณหภูมิเฉลี่ยรายวันของทุกดิสก์
  (สีต่างกันต่อดิสก์ + legend) เห็นตัวที่ร้อนกว่าเพื่อนทันที

### Changed

- ⚙️ **ค่าเริ่มต้นตัวเลขบน tray ใหม่**: สี**ขาว** · **regular** · **Arial**
  (เดิม auto/bold/Segoe UI) — ใช้ได้ทันทีกับการติดตั้งใหม่ ค่าที่ตั้งไว้แล้ว
  ไม่ถูกแตะ
- 📝 **Auto Record History เปิดเป็นค่าเริ่มต้น** — บันทึกประวัติอุณหภูมิ
  อัตโนมัติตั้งแต่ติดตั้ง (ปิดได้ใน Settings → General)

### Verified

- 🌡️ โพรบก่อน build: source code ล่าสุดอ่าน SMART จริง (temp=37°C) แสดงผลครบ
  (tooltip + ไอคอน) และฟีเจอร์ใหม่ทั้งหมดทดสอบกับข้อมูลจริง

## [1.18.0] — 2026-09-23

### Added

- 📊 **กราฟ wear + uncorrected read errors ในรายงานรายสัปดาห์** — นอกจากกราฟ
  อุณหภูมิเดิม รายงานต่อดิสก์ตอนนี้มี 3 กราฟ SVG (อุณหภูมิ/การสึก/ข้อผิดพลาดการอ่าน
  ที่แก้ไม่สำเร็จ — ตัดที่ 500) ให้เห็นแนวโน้มเต็มภาพในไฟล์เดียว
- 💾 **ปุ่ม Export... ในแท็บ Health** — เลือกที่เซฟรายงานรายสัปดาห์เอง
  (Save As dialog, ตั้งชื่อไฟล์อัตโนมัติตามวันที่) นอกจากเปิดในเบราว์เซอร์ทันที

### Fixed

- 🩹 แก้เศษโค้ดรายงานที่อ้างตัวแปรที่ไม่มีอยู่ (NameError ตอนสร้างกราฟ error)
  จากการแก้ที่ถูกขัดจังหวะ — คำนวณค่า uncorrected รายวันจาก CSV ตรง ๆ

### Verified

- 🌡️ โพรบก่อน build: source code ล่าสุดอ่าน SMART จริง (temp=37°C) และแสดงผลครบ
  (tooltip `37°C` + ไอคอน 64×64) พร้อมทดสอบฟีเจอร์ใหม่กับข้อมูลจริง

## [1.17.0] — 2026-09-23

### Added

- 📄 **รายงานสุขภาพรายสัปดาห์ (HTML)** — ปุ่มใหม่ในแท็บ Health สรุป 7 วันล่าสุด
  รายดิสก์: กราฟ SVG อุณหภูมิเฉลี่ยรายวัน + ตาราง avg/min/max/wear
  (ไฟล์เดียวจบ ไม่พึ่งเน็ต ส่งต่อ/แนบเมลได้) เปิดในเบราว์เซอร์ทันที
- 📉 **เตือน wear เพิ่มเร็วผิดปกติล่วงหน้า** — วิเคราะห์ความชันจากสถิติรายวัน:
  ถ้า wear พุ่ม ≥2 จุดใน 7 วัน จะเตือน**ก่อน**ถึงเขต 75/90% (กันเตือนซ้ำ
  รายดิสก์ทุก 7 วัน)

### Changed

- 🧳 **โหมด portable เก็บครบทุกไฟล์ในโฟลเดอร์ตัวเอง** — ย้ายประวัติอุณหภูมิ
  (`ssd_temp_history.csv`) จาก %TEMP% มาไว้ใน DATA_DIR เป็นไฟล์สุดท้าย
  ที่ยังหลุดออกนอกบันเดิล (config/log/geometry/health/smart state อยู่ครบแล้ว)

### Verified

- 🌡️ โพรบก่อน build: source code ล่าสุดอ่าน SMART จริง (temp=37°C) และแสดงผล
  icon/tooltip ถูกต้อง + รายงานสร้างได้จริงจากข้อมูลจริง
- 🧪 pytest 248 เคส (+7) · E2E rollback ผ่านทั้งสองซีน

## [1.16.0] — 2026-09-23

### Added

- 🚨 **แจ้งเตือน SMART proactive** — เตือนทันที (มี cooldown) เมื่อตัวนับ
  "อ่านไม่สำเร็จ" **เพิ่มขึ้น** หรือการสึก (Wear) ข้ามเข้าเขตเสี่ยงใหม่
  (75% / 90%) แม้อุณหภูมิยังเขียว · เปิด/ปิดได้ใน Settings (General) ·
  สถานะ baseline เก็บในไฟล์ ไม่เตือนซ้ำสิ่งที่รู้แล้วหลังรีสตาร์ท และ
  การอ่านครั้งแรกตั้ง baseline เงียบ ๆ (กัน alert storm บนดิสก์เก่า)
- 📈 **สถิติสุขภาพรายวัน + แนวโน้ม 30 วัน** — แอปบันทึกวันละหนึ่งแถวต่อดิสก์
  (อุณหภูมิ/wear/ตัวนับ error) ลง `health_daily.csv` อัตโนมัติ ดูย้อนหลัง
  ได้จากปุ่ม **30-day trend** ในแท็บ Health (สรุป avg/min/max/wear รายวัน)
- 🛡️ **กัน autostart ชี้ pythonw** — เมื่อรันจากซอร์ส แอปจะจด Run key ชี้
  **exe ที่ติดตั้ง** เสมอ (แก้บั๊กที่ทำให้แอปซอร์สถือ mutex บล็อก self-update
  แบบเงียบ ๆ) พร้อม self-heal ค่าเก่าที่ผิดตอน startup

### Verified

- 🌡️ โพรบก่อน build: อ่าน SMART จริงด้วยแอดมิน (temp=37°C) และฟีเจอร์ใหม่ทั้งหมด
  ทำงานถูกต้องกับข้อมูลจริง (baseline เงียบ, CSV เขียนจริง, entry point ถูกไฟล์)
- 🧪 pytest 241 เคส (+11)

## [1.15.1] — 2026-09-23

### Fixed

- 🩹 **เวอร์ชันใหม่ที่บูตสำเร็จจดตัวเองลง blacklist** — พบคืนวัน release ของ
  1.15.0: marker `update_rollback_reported` ค้างจากรอบเก่า (เช่น เทส/อัปเดตที่
  relaunch ถูกขัด) ทำให้ `begin_healthy_session` เข้าสู่สาขา rollback ผิด ๆ
  → เวอร์ชันที่เพิ่งติดตั้งสำเร็จถูกจดลง `update_broken_versions.txt` และเด้ง
  dialog เตือนผิด แก้โดยเช็ค**อายุ marker**: เก่ากว่า 180 วิ = เศษจากรอบเก่า
  ให้ล้างทิ้งแล้วเดิน healthy path ปกติ (marker สด = rollback จริง ยังทำงานเหมือนเดิม)

## [1.15.0] — 2026-09-23

### Added

- 🩺 **แท็บ Health ในหน้าต่าง Settings** — สรุปสุขภาพ SMART รายดิสก์
  (อุณหภูมิ / การสึก Wear / ตัวนับข้อผิดพลาดการอ่าน + แก้ไม่สำเร็จ) พร้อมสรุปสถานะ
  "ปกติ / ควรตรวจสอบ" ปุ่มรีเฟรชสอบถาม SMART ใหม่แบบ background (ไม่ค้างหน้าต่าง)
  และแปลครบ 4 ภาษา (en/th/ja/zh)

### Changed

- 🧪 **E2E rollback ผ่านซีนจริงทั้งสองแบบ**: ซีน A (exe ใหม่บูตพัง → watchdog คืน
  เวอร์ชันเดิม + รายงาน + จดจำเวอร์ชันพัง) และซีน B (บูตสำเร็จ → watchdog ไม่แตะ
  อะไรเลย) รวมถึงยืนยันว่าการทดสอบทั้งหมดไม่เขียนไฟล์จริงของเครื่อง (log delta = 0)
- 🌡️ **ตรวจสอบก่อน build**: query SMART ตัวเดียวกับแอปยืนยันการอ่านค่าจริง
  (temp=37°C, wear=0) และการแสดงผลบนไอคอน/tooltip ก่อนปล่อยเวอร์ชัน

## [1.14.1] — 2026-09-22

### Fixed

- 🌡️ **อ่านอุณหภูมิไม่ได้เลย (dialog "No SSD temperature data")** — บั๊กจาก
  v1.14.0: query เขียน `$c.Prop|ReadErrorsTotal` ทำให้ PowerShell ตีความ
  `ReadErrorsTotal` เป็น**คำสั่ง** (ไม่ใช่ property) แล้วพังทั้ง statement
  ทุกรอบ loop → ไม่มีข้อมูลส่งกลับแม้รันแบบ Administrator
  (พิสูจน์ด้วยการรับ stderr จริง: `The term 'ReadErrorsTotal' is not recognized`)
  แก้เป็น property access ตรง ๆ `$c.ReadErrorsTotal`

### Added

- 🛟 **Rollback อัตโนมัติของระบบอัปเดต** — ก่อนติดตั้งทุกครั้ง แอปเก็บ exe ตัวเอง
  ไว้เป็น `ssd_temp_monitor.prev.exe` + เขียน pending marker ถ้าเวอร์ชันใหม่
  **ไม่บูตสำเร็จภายใน 90 วินาที** shim จะ kill, คืน exe เดิม, เปิดแอปใหม่
  และแจ้งผู้ใช้ด้วย dialog (แอปเก่าเป็นผู้แจ้ง — shim รันแบบไม่มี UI)
- 🚫 **จำเวอร์ชันที่พัง** — เวอร์ชันที่ถูก rollback ถูกบันทึกไว้และ updater จะ
  **ข้าม** (กันวงจรอัปเดตซ้ำเวอร์ชันเดิมตลอดไป) บันทึกล่าสุด 10 รายการ
- 🔁 **เมนู "คืนเวอร์ชันก่อนหน้า"** — ผู้ใช้ย้อนกลับเองได้จาก tray menu
  (ยืนยันก่อน, รีสตาร์ตผ่าน explorer แบบ detached เหมือน updater)
- 🧪 pytest **226 เคส** (+9)

## [1.14.0] — 2026-09-22

ชุดฟีเจอร์ 10 ข้อ — พิสูจน์ผ่านช่องทาง pre-release ด้วย rc1/rc2 ก่อนปล่อย stable
(rc2 build ด้วย PyInstaller 6.22.3 และ self-update จาก v1.13.0 สำเร็จ)

### Added

- 🚀 **เริ่มอัตโนมัติตอนเปิดเครื่อง** — checkbox ใน Settings (registry `HKCU\...\Run`
  ของผู้ใช้ปัจจุบัน ไม่ต้องแอดมินเพิ่ม) + installer มีตัวเลือกตั้งตอนติดตั้งอยู่แล้ว
- 📈 **ประวัติได้ถึง 24 ชั่วโมง** (เดิม cap 4 ชม.) + **ปุ่ม Export CSV / PNG**
  ในหน้าต่างกราฟ (บันทึกผ่าน save dialog, PNG จับภาพ canvas จริง)
- 🔔 **Toast คลิกได้** — การแจ้งเตือนทั้งหมด (อัปเดต/อุณหภูมิเกิน) คลิกแล้วเปิดแอป
  (ลงทะเบียนโปรโตคอล `ssdtempmon:` ใน HKCU อัตโนมัติ ไม่เพิ่ม dependency)
- 🗔 **จำขนาด/ตำแหน่งหน้าต่าง** — กราฟ/รายละเอียด/ดิสก์จำ geometry ลง
  `window_geometry.json` เปิดครั้งหน้าอยู่เดิม
- 💬 **Tooltip แบบย่อ** — `65°C · 58°C (2 disks)` (ปิดได้ใน Settings ให้แสดงชื่อดิสก์เต็ม)
- 🎒 **โหมด Portable จริง** — วางไฟล์ marker (`portable_data.portable`) เคียง exe
  (หรือใช้ zip portable จาก release) config/ประวัติ/log จะอยู่ในโฟลเดอร์
  `portable_data\` เคียง exe ทั้งหมด — เหมาะกับ USB ไม่ทิ้งร่องรอยบนเครื่องอื่น
- 🌐 **ภาษาญี่ปุ่น + จีน** — UI ครบทุกหน้าต่าง/เมนู/แจ้งเตือน (92 คีย์ × 4 ภาษา,
  เทสบังคับ parity), เมนู Language บน tray เพิ่ม 日本語 / 中文
- 🩺 **สัญญาณสุขภาพ SSD** — อ่าน Wear และ Read Errors จาก reliability counter
  แสดงเตือนในหน้ารายละเอียด: สึก ≥75 % / ≥90 % (แนะนำสำรอง), read error
  แก้ไม่ได้ ≥1 (วิกฤต), แก้ไขแล้ว ≥100 ครั้ง

### Fixed

- 🔢 **semver ของ pre-release ผิดลำดับ** — เดิม `1.14.0-rc1` เทียบเป็น `1.14.0`
  ทำให้ช่องทาง pre-release ไม่เห็น rc ใหม่ และ rc ล่าสุดไม่อัปเกรดขึ้น stable
  ตอนนี้ `1.14.0-rc1 < 1.14.0-rc2 < 1.14.0` ตาม semver (เช็คนี้เข้า self-test แล้ว)

### Changed

- 📦 release มี 4 assets: เพิ่ม `..._portable.zip` (มีโฟลเดอร์ `portable_data`)
- 🧪 pytest **217 เคส** (+31)

## [1.13.0] — 2026-09-22

### Added

- ➕ **ปุ่ม Apply ใน Settings** — บันทึกและใช้ค่าทันที**โดยไม่ปิดหน้าต่าง**
  เหมาะกับการลองค่าซ้ำ ๆ (Save = Apply + ปิด, ทุกค่ายังมีผลทันทีเหมือนเดิม)
- 🔠 **เลือกฟอนต์ตัวเลขได้ 13 ตระกูล** (เดิม 4 + auto) — Segoe UI, Arial,
  Tahoma, Verdana, Calibri, Candara, Corbel, Franklin Gothic, Georgia,
  Trebuchet MS, Consolas, Times New Roman, Courier New — ทุกตระกูลมากับ
  Windows 10/11 และมี fallback อัตโนมัติถ้าเครื่องไหนขาดไฟล์ฟอนต์
- 🅰️ **เลือกสไตล์ตัวเลขได้** — Regular / Bold / Italic / Bold Italic
  (ตระกูลที่ไม่มี italic จริง เช่น Tahoma จะสังเคราะห์หรือ fallback ให้เอง)
- 📏 **ปรับขนาดตัวเลขได้** — 50–150 % ของขนาดอัตโนมัติ เลข 3 หลักที่ใหญ่ขึ้น
  ยังถูกย่อพอดีเม็ดอัตโนมัติเหมือนเดิม (ไม่มีทางล้น)
- 🎨 **พรีเซ็ตสีตัวเลขเพิ่มเป็น 8 ค่า + ช่องพิมพ์เองได้** — auto, ขาว, ดำ,
  เหลือง, แดง, ฟ้า, เขียว, ชมพู — ช่องสีเป็น editable combobox พิมพ์
  `#rrggbb` ใดก็ได้
- 🧪 **เมนู "ทดสอบระบบอัปเดตด้วยตัวเอง"** — รัน 12 เช็คจำลองวงจรอัปเดตจริง
  (เปรียบเทียบเวอร์ชัน, เลือกไฟล์ release, ตรวจ SHA-256, สัญญาของ shim:
  กัน env ค้าง + relaunch detached) แล้วสรุปผลให้เป็นข้อความเดียว —
  ไม่ยุ่งเน็ตเวิร์ก ไม่รันอะไรจริง คลิกได้ทุกเมื่อ
- 🧹 **เก็บกวาดโฟลเดอร์ `_MEI` ค้างตอนเปิดโปรแกรม** — โฟลเดอร์ชั่วคราวของ
  onefile ที่เหลือจากครั้งที่แอปโดน kill/crash จะถูกลบทิ้ง (ตรวจว่าโฟลเดอร์
  "ยังถูกล็อกโดยโปรเซสมีชีวิต" ด้วย rename-probe + เว้นไฟล์ที่สร้างใหม่
  60 วิ และข้ามโฟลเดอร์ของตัวเอง — ไม่มีทางลบของแอปที่กำลังรัน)

### Changed

- 🔄 ค่าตั้งเดิม `icon_font: "auto"` ใน config.json ถูกแปลงเป็น
  `"Segoe UI"` + style `bold` อัตโนมัติ (ภาพไอคอนเหมือนเดิมทุกพิกเซล)
- 🧪 pytest **186 เคส** (+19: ตารางฟอนต์/สไตล์/สเกล, พรีเซ็ตสี, self-test,
  cleanup `_MEI` รวมเคส rename-probe ถูกล็อก)

## [1.12.1] — 2026-09-22

### Fixed

- 💥 **Dialog "Failed to load Python DLL ..._MEIxxxxx\\python3xx.dll"** —
  กรณีพี่น้องกับปัญหา parent-guard ของ 6.22: แอปที่ถูก relaunch ถ้าได้รับ
  ตัวแปรแวดล้อม `_MEIPASS2` ตัวเก่าจะ**ข้ามการแตกไฟล์** แล้วไปหา DLL ใน
  โฟลเดอร์ชั่วคราวของ updater ที่ถูกลบไปแล้ว shim ตอนนี้**ล้างตัวแปร
  `_MEIPASS2` / `_PYI_*` ทั้งชุดก่อน** เรียก installer/relaunch
  (เครื่องที่ยังเห็น dialog นี้ตอนอัปเดตจาก v1.12.0 = ตัว shim เก่า —
  อัปเดตถึง v1.12.1 แล้วจะไม่เกิดอีก)
- 🧪 เทสไอคอน flaky (global `SETTINGS` รั่วข้ามเทส) — เพิ่ม autouse
  fixture ที่ snapshot/restore `SETTINGS` + `_ICON_CACHE` ทุกเคส

## [1.12.0] — 2026-09-22

### Fixed

- 🚑 **เปิดหน้า Settings ไม่ได้ (v1.11.0)** — ตัวอย่างไอคอนใช้รูปแบบ
  `preview_lbl.image = preview_lbl.image` ที่ทำให้ PhotoImage ใหม่ไม่มีใคร
  อ้างอิง ถูก GC ทำลายภาพ หน้าต่างจึงวาดไม่ได้ แก้: เก็บ reference จริง
  (`photo = tk.PhotoImage(...)` → `lbl.image = photo`) และ preview ทั้งก้อน
  ถูก wrap ด้วย try/except — แม้ preview พัง หน้า Settings ต้องเปิดได้เสมอ
- 🛡️ **Dialog "Security validation failure: invalid originating onefile
  parent process (PID not found)"** — ไม่ใช่ไวรัสหรือแอปเสีย แต่เป็นกลไก
  security ใหม่ของ **PyInstaller 6.22** (GHSA-9fxf-4qw3-ghmr) ที่ให้ onefile
  child ตรวจว่า parent process ยังมีชีวิตอยู่ ซึ่งขัดกับห่วงโซ่ relaunch
  ของการอัปเดตอัตโนมัติ (updater → cmd shim → installer → แอปใหม่)
  แก้ 2 ชั้น: pin `pyinstaller<6.22` ในทุก build และ shim เปลี่ยนไป
  relaunch ผ่าน `explorer.exe` (detached — ไม่มี parent ให้ตรวจ) พร้อมลด
  blind window ของ shim จาก ~2 s เหลือ ~1 s

### Added

- 🗂️ **หน้า Settings แบบแท็บ** — General / Icon / Updates (รองรับการตั้งค่า
  ที่เพิ่มขึ้นเรื่อย ๆ โดยไม่ต้องเลื่อนหน้าต่างยาว ๆ)
- 🎨 **ธีมไอคอนสำเร็จรูป** — Classic / Minimal / Mono / Neon (+ Custom):
  เลือกครั้งเดียว ตั้งฟอนต์ สีตัวเลข และ high-contrast ให้ครบ พร้อม
  **preview ใหญ่ 2 ขนาด** (ขนาดจริงบน tray + 96 px) อัปเดตสดขณะปรับ

### Developer

- 🧪 pytest **167 เคส** (+5: theme validation, สีในธีมถูกต้อง, regression
  guard ของ preview reference, โครงสร้างแท็บ)

## [1.11.0] — 2026-09-22

### Added

- 🔤 **ปรับแต่งตัวเลขบน tray ได้จาก Settings** — เลือกฟอนต์ (Auto / Arial /
  Segoe UI / Tahoma / Verdana), สีตัวเลข (`auto` = ตัดกับพื้นอัตโนมัติ
  หรือกำหนด `#rrggbb` เอง) และ**เลื่อนตำแหน่งตัวเลขแกน X/Y** (−50..50 px
  พร้อม clamp ไม่ให้เลขหลุดออกนอกเม็ด)
- 👁️ **Live preview ในหน้า Settings** — เห็นไอคอนตามค่าที่กำลังปรับทันที
  ก่อนกดบันทึก (รวมขนาด/ฟอนต์/สี/ตำแหน่ง/high-contrast)
- 🌐 **สลับภาษาจากเมนู tray** — เมนูย่อย Language / ภาษา (English / ไทย)
  เปลี่ยนภาษา UI ทันทีและจำค่า ไม่ต้องเข้าหน้า Settings

### Fixed

- 📐 ตัวเลขบนไอคอนจัดกึ่งกลาง**แนวตั้ง**ถูกต้อง (เดิมคำนวณ center จาก
  ขนาดไอคอนแทนความสูงจริงของตัวเลข ทำให้เลขลอยชิดขอบบน)

### Developer

- 🧪 pytest **161 เคส** (+11: validation ฟอนต์/สี/ตำแหน่ง, offset ขยับเลขจริง
  บนพิกเซล, สี custom ตกลงบนพิกเซล, เมนูภาษา persist)

## [1.10.0] — 2026-09-22

### Added

- 🌐 **เลือกภาษา UI ได้ (ไทย/อังกฤษ)** — เมนู tray, หน้า Settings, About,
  หน้าต่างทุกใบ และการแจ้งเตือนทั้งหมด แปลตามที่เลือกใน
  Settings → **Language / ภาษา** (ค่าเริ่มต้น en, บันทึกใน config.json
  เมนู rebuild ทันทีไม่ต้องรีสตาร์ท)
- 🔐 release workflow: ขั้น **Verify signatures** — ถ้าเปิดเซ็นโค้ดไว้
  ไฟล์ที่เซ็นต้องผ่าน `signtool verify /pa /all` ไม่งั้น release พังทันที

### Changed

- 🎨 **ไอคอน tray อ่านง่ายขึ้นมาก** — เม็ดสีขยายเต็มไอคอน (ขอบ 1-2 px)
  ให้ตัวเลขมีพื้นที่มากขึ้น · สีตัวเลขปรับตามความสว่างของเม็ดอัตโนมัติ:
  **เลขดำเข้มบนเม็ดเขียว/ส้ม** (contrast ~9:1 แทนขาว 2:1 เดิม) และ
  **เลขขาวบนเม็ดแดง** + เส้น stroke รอบตัวเลขกันเบลอตอนขนาดเล็ก
  · ตัวเลข 3 หลัก (เช่น 100) ย่อขนาดพอดีเม็ดอัตโนมัติ

### Developer

- 🧪 pytest **150 เคส** (+9: i18n parity/fallback/validate, สีเลขตาม
  luminance, เม็ดครอบพื้นที่, ฟอนต์ fit ตัวเลข 3 หลัก)

## [1.9.0] — 2026-09-22

เวอร์ชันเสถียรรวมทุกอย่างจาก [1.9.0-rc1] พร้อมเพิ่มเติม:

### Added

- 🎨 **ปรับขนาดไอคอนได้ (16–128 px)** และ **โหมด high-contrast**
  (เม็ดดำขอบขาว — อ่านง่ายบน taskbar สีอ่อน) ตั้งในหน้า Settings
  มีผลทันทีไม่ต้องรีสตาร์ท · ไอคอนทุกตำแหน่ง (tray, ต่อดิสก์, tooltip)
  ใช้การตั้งค่าเดียวกัน
- 📋 **Event log หมุนเวียน** — เริ่ม/ปิดโปรแกรม, การแจ้งเตือน,
  การตรวจ/ติดตั้งอัปเดต และดิสก์ที่หาย/เพิ่ม ถูกบันทึกลง
  `%APPDATA%\SSDTempMonitor\ssd_temp_monitor.log` (512 kB × 3 ไฟล์)
- 🌐 **คู่มือภาษาอังกฤษฉบับเต็ม** — [USER_GUIDE_EN.md](USER_GUIDE_EN.md)
  + [USER_GUIDE_EN.html](USER_GUIDE_EN.html) ลิงก์จาก root README,
  คู่มือไทย และหน้า GitHub Pages

### Added

- 🏷️ **ฝังเวอร์ชันจริงใน exe (VERSIONINFO)** — Properties ของไฟล์เห็นเวอร์ชัน
  สร้างอัตโนมัติจาก APP_VERSION ทุก build และ updater ใช้เวอร์ชันของ exe
  ที่ติดตั้งจริงเป็นตัวเทียบ (`effective_version()`)
- ℹ️ **About โชว์เวอร์ชันล่าสุดแบบสด** — ดึงจาก GitHub บนเธรดแยก
  อัปเดตผลบน UI thread ปลอดภัย พร้อมสีเขียว/เหลืองเมื่อมีเวอร์ชันใหม่
- 🩹 **Copy diagnostics** — เมนูคัดลอกข้อมูลวินิจฉัย (เวอร์ชัน, admin,
  settings, ดิสก์ทั้งหมด) ไปคลิปบอร์ดสำหรับแนปให้ AI/ซัพพอร์ต
- 🔁 **ปรับ poll interval มีผลทันที** — ไม่ต้องรีสตาร์ตอีกต่อไป

### Changed

- 🧯 **แจ้งเตือนอัปเดตครั้งเดียว/เวอร์ชัน** — ไม่ spam ทุกรอบเช็ค
  (การเช็คเอง/หน้า About ยังรายงานปกติทุกครั้ง)
- 🧹 **ถอนติดตั้งลบ config.json ด้วย** (เดิมทิ้งค้างไว้)

### Developer

- 🧪 pytest 126 เคส (+12)
- release.yml: notes ดึงจาก CHANGELOG เวอร์ชันนั้นอัตโนมัติ + เตือนถ้า
  tag/เวอร์ชัน/วันที่ไม่ตรงเอกสาร + tag แบบ `x.y.z-rc1` กลายเป็น
  **prerelease: true** โดยอัตโนมัติ

## [1.8.1] — 2026-09-21

### Fixed

- 🔄 **แอปกลับมาที่ tray หลัง self-update เอง** — ตัวติดตั้งแบบเงียบข้าม
  postinstall launch ไป ทำให้หลังอัปเดตสำเร็จแอปหายไปจาก tray ตัว shim
  จึงเป็นคนเริ่มแอปใหม่เองหลังติดตั้งสำเร็จ (`if not errorlevel 1 start`)

## [1.8.0] — 2026-09-21

### Added

- 🧩 **แก้บั๊ก auto-update ติด AppMutex** — ตอนนี้ updater เขียน cmd shim
  ที่**รอให้แอปออกก่อน** (ตรวจ process ทุกวินาที ด้วย `tasklist`/`find`
  path เต็มจาก System32 กันชน GNU find ของ Git Bash) แล้วค่อยรันตัวติดตั้ง
  — เดิม installer เงียบ ๆ abort (exit 1) เพราะแอปยังถือ mutex อยู่
- ⚡ **`--update-now` flag** — ตรวจ ดาวน์โหลด ตรวจ SHA-256 และติดตั้ง
  แบบไม่มี UI (พร้อม `UPDATE-RESULT:` ทาง stdout สำหรับระบบอัตโนมัติ)
- 🔀 **ช่องทางอัปเดต (update channel)** — เลือกได้ใน Settings:
  `stable` หรือ `pre-release` (tag แบบ `v1.9.0-rc1` จะถูกเห็นเฉพาะ
  ช่องทาง pre-release)
- ⏱️ **ช่วงเวลาเช็คอัปเดตปรับได้** — 5–1440 นาที (มีผลทันที ไม่ต้องรีสตาร์ต)
- 🤝 **รันหลังติดตั้งในบริบทผู้ใช้เดิม** — `[Run]` ใช้ `runasoriginaluser`
  ทำให้แอปกลับมาบน desktop ของผู้ใช้จริงหลัง auto-update (ไม่ค้างใน
  บริบท admin)
- 🖊️ **ขั้น sign โค้ดใน release workflow** — เปิดใช้เมื่อตั้ง secrets
  (`SIGNING_PFX_BASE64` ฯลฯ) ดู `docs/CODE_SIGNING.md` — ไม่ตั้ง =
  ข้ามไปเลย ปล่อยแบบ unsigned ตามเดิม

### Fixed

- แก้เทส fake `urllib` ที่อ่าน attribute ผิด (`req.url` → `req.full_url`)
  และปรับ assertion ให้ตรง flow การแจ้งเตือนจริงของ updater

## [1.7.0] — 2026-09-21

### Added

- 🔐 **ตรวจ checksum ก่อนอัปเดต** — updater ดาวน์โหลด `SHA256SUMS.txt`
  จาก release แล้วตรวจ SHA-256 ของตัว installer ก่อนบันทึก/รันทุกครั้ง
  ถ้าค่าไม่ตรงจะยกเลิกทันที (ไฟล์ถูกแก้/ดาวน์โหลดเสียหาย = ไม่มีการติดตั้ง)
- ℹ️ **หน้า About** — เวอร์ชัน ลิงก์ไปหน้า releases และปุ่มเช็คอัปเดต
- 📦 Release แนบ `SHA256SUMS.txt` ให้ด้วยเสมอ (สร้างอัตโนมัติใน release workflow)

### Fixed

- แก้เทส fake `urllib` ที่อ่าน attribute ผิด (`req.url` → `req.full_url`)
  และปรับ assertion ให้ตรง flow การแจ้งเตือนจริงของ updater

## [1.6.0] — 2026-09-21

### Added

- 🔄 **Auto-update** — เช็ค GitHub Releases อัตโนมัติหลังเปิดโปรแกรม
  และทุก 6 ชั่วโมง เมื่อมีเวอร์ชันใหม่จะแจ้งผ่าน notification
  กด **Check for updates...** ในเมนูเพื่อดาวน์โหลดและติดตั้งได้ทันที
  (installer จะปิดแอป ติดตั้ง แล้วเปิดใหม่เองผ่าน `/CLOSEAPPLICATIONS`)
  ปิดการเช็คได้ด้วย `"check_updates": false` และเปลี่ยน repo ต้นทางได้
  ผ่าน `"github_repo"` ใน config.json

## [1.5.0] — 2026-09-21

### Added

- 💺 **ไอคอนแยกต่อดิสก์** — เครื่องที่มี NVMe/SATA SSD หลายตัว จะมี tray icon
  เพิ่มโดยอัตโนมัติ ดิสก์ละ 1 ไอคอน (ไอคอนหลักโชว์ดิสก์ที่ร้อนที่สุดตามเดิม)
  ไอคอนถูกเพิ่ม/ลบตามการเสียบ/ถอดดิสก์ ปิดได้ด้วย
  `"multi_disk_icons": false` ใน config.json
- 🧪 **CI ทดสอบ installer จริง** — job `installer-test` ติดตั้งแบบ silent,
  ตรวจไฟล์ใน Program Files, ทดสอบ duplicate instance (exit code 2),
  และถอนติดตั้ง ทุก push
- 🚀 **Release automation** — push tag `v*` เข้าสู่งาน build + smoke test
  แล้วสร้าง GitHub Release อัตโนมัติพร้อมแนบ exe (portable) และ installer

### Changed

- pytest suite เพิ่มเป็น 79 เคส (ต่อดิสก์ lifecycle 4 เคส)

## [1.4.0] — 2026-09-21

### Fixed — หน้าต่าง History ปิดไม่ได้ / tray ค้างตอน Exit

- **สาเหตุ:** หน้าต่างกราฟรัน `mainloop()` บนเธรดของเมนู pystray
  (ต่างจากหน้าต่างอื่นที่ผ่าน `_spawn_once`) ทำให้ tray ถูกบล็อก —
  กด Close ไม่ตอบสนอง และ Exit ค้างตาม บวกการเรียก tk method
  ข้ามเธรดตอนเปิดซ้ำ (`deiconify/lift`) ซึ่งทำให้ tk interpreter พัง
- **วิธีแก้:** กราฟผ่าน `_spawn_once` เหมือนหน้าต่างอื่น (เธรดของตัวเอง),
  เปิดซ้ำ = ยกหน้าต่างเดิมด้วย Win32 (`FindWindowW`/`SetForegroundWindow`)
  แทน tk, และการออกจากโปรแกรมเปลี่ยนเป็น "ตั้งธง `_shutdown_requested`
  แล้วรอเธรดกราฟปิดตัวเอง" (timeout 3 วินาที กันค้าง) — ไม่มี tk call
  ข้ามเธรดอีกต่อไป
- **ทดสอบการออกจากโปรแกรม:** E2E จำลองกด Exit ขณะกราฟเปิด —
  icon.run() คืนค่าใน 2.6 วินาที, history flush, โปรเซสจบสนิท
  พร้อม unit test ครอบ quit() ทุกเส้นทาง

### Added

- ⚙️ **หน้า Settings** (เมนู → Settings...) — แก้ polling interval,
  alert threshold/sustain/cooldown, ความยาว history และ record-on-start
  บันทึกลง `%APPDATA%\SSDTempMonitor\config.json` มีผลทันที
  (ยกเว้น poll interval ที่ใช้หลังรีสตาร์ท) ค่าผิดช่วงถูก clamp อัตโนมัติ
- 🤖 **GitHub Actions CI** (`.github/workflows/ci.yml`) — รัน pytest
  บน windows-latest แล้ว build exe + installer เป็น artifacts ทุก push
- 🧪 **ทดสอบติดตั้ง/ถอนติดตั้ง/single-instance จริง**: installer ติดตั้งลง
  Program Files (แก้ `ArchitecturesInstallIn64BitMode` — เดิมหลุดไป x86),
  uninstall/reinstall ผ่าน, mutex ถูกยืนยันด้วย external probe
  (held ขณะรัน = error 183, ปล่อยหลังปิด = 0) และอินสแตนซ์ซ้ำ
  **ออกด้วย exit code 2** — มนุษย์เห็น MessageBox, ระบบอัตโนมัติใช้
  flag `--duplicate-silent` (env `SSD_TEMP_SILENT_DUPLICATE=1` ก็ได้ —
  แต่ระวัง UAC elevation **ไม่ส่งต่อ environment variables**)

### Changed

- ค่าคงที่ทั้งหมด (poll/alert/history) มาจาก config.json ผ่าน
  `load_settings()` แทนการ hardcode — env `SSD_TEMP_RECORD_HISTORY`
  ยังใช้ override ได้
- pytest suite เพิ่มเป็น 71 เคส (รวม regression guard ของบั๊กนี้)

## [1.3.0] — 2026-09-21

### Added

- 🔔 **แจ้งเตือนอุณหภูมิเกิน** — notification ของ Windows เมื่อดิสก์ร้อน
  ≥ 65 °C **ติดต่อกัน 30 วินาที** (กันตัวเลขกระโดดหลอก) และเตือนซ้ำ
  ไม่ถี่กว่าทุก **5 นาที** (cooldown) หากอุณหภูมิลดลงต่ำกว่าเกณฑ์
  นาฬิกาจะรีเซ็ตใหม่ — logic อยู่ใน `alert_state()` ที่ทดสอบแยกได้
- 📈 **กราฟแบบ real-time** — หน้าต่างกราฟวาดใหม่ทุก 1 วินาที
  ไม่ต้องปิด-เปิดใหม่ และเปิดเมนูซ้ำจะ**โฟกัสหน้าต่างเดิม**แทนที่จะเปิดซ้อน
- 🚦 **Single instance** — เปิดโปรแกรมซ้ำจะขึ้นข้อความแล้วออก
  (Windows named mutex `SSDTempMonitor_SingleInstance`)
- 🧪 **pytest suite 55 เคส** (`tests/`) — รันได้โดยไม่ต้องมี admin,
  PowerShell หรือ GUI พร้อม regression guard กันบั๊กเดิม
  (ต้องกรอง USB ก่อนอ่าน counter / ห้าม `int("")` พัง)
- 📦 **setup.iss** — สคริปต์ Inno Setup สร้างตัวติดตั้ง
  `installer\ssd_temp_monitor_setup.exe` พร้อมตัวเลือก auto-start on login,
  desktop icon, และ AppMutex ที่ผูกกับ single-instance ของแอป
  (ห้ามติดตั้ง/ถอนติดตั้งขณะแอปรันอยู่)

### Changed

- exe ขอสิทธิ์ admin ผ่าน **manifest โดยตรง** (`--uac-admin`) —
  Windows จะแสดง shield บนไอคอน และไม่ต้องพึ่ง self-elevate เป็นทางหลัก
- `build_exe.bat` สั่ง build installer ต่อได้ด้วย `iscc setup.iss`

## [1.2.0] — 2026-09-21

### Fixed — polling ค้าง/timeout เมื่อเสียบ USB reader (ผลวิจัยเพิ่มเติม)

- พบว่า bridge chip USB (ASM236X) ไม่ได้แค่รายงาน temp เป็น 0 แต่**ทำให้การอ่าน
  `Get-StorageReliabilityCounter` ค้างถึง 42 วินาที** (วัดจริง: NVMe 0.1 s,
  USB 42.1 s) ทำให้ query เดิมที่อ่าน counter ทุกดิสก์ชน timeout 30 s และได้ค่าว่าง
- แก้ด้วยการแยกเป็น 2 query: กรอง USB เสร็จ**ก่อน**อ่าน counter
  (`PS_TEMPS` — เหลือ ~1 s) ส่วนรายชื่อ debug ใช้ query แยกที่ไม่อ่าน counter เลย

### Added

- เมนู **Show all disks (debug)** — แสดงทุกดิสก์พร้อม MediaType / BusType
  และเครื่องหมาย ✓/✗ ว่าตัวไหนถูกนับหรือถูกกรอง พร้อมอุณหภูมิ (USB แสดง n/a)
- เมนู **Record history** (เปิด/ปิดได้) + **Show temperature graph** —
  กราฟย้อนหลัง 30 นาทีพร้อม min/max/avg เก็บลง CSV ที่ `%TEMP%\ssd_temp_history.csv`
  (auto-flush ทุก 60 s, ตั้งค่าเริ่มต้นด้วย env `SSD_TEMP_RECORD_HISTORY=1`)
- แจกจ่ายเป็น **exe สำเร็จรูป** `dist\ssd_temp_monitor.exe` (PyInstaller,
  ผู้ใช้ไม่ต้องติดตั้ง Python) พร้อม `build_exe.bat` และ `make_icon.py`
  สำหรับ rebuild + ไฟล์ไอคอน `app_icon.ico`

### Changed

- Self-elevate (UAC) รองรับทั้งสคริปต์ `.py` และ exe ที่ถูก freeze แล้ว
- ไอคอน tray มี cache — ลดการวาดภาพซ้ำทุกวินาที

## [1.1.0] — 2026-09-21

### Fixed — อ่านอุณหภูมิผิดพลาดเมื่อเสียบ USB card reader

- กรองดิสก์ที่ต่อผ่าน **BusType USB** ออกจากการอ่านอุณหภูมิทั้งหมด
  เพราะ USB card reader / external enclosure หลายรุ่นถูก Windows
  รายงานเป็น `MediaType = 'SSD'` แต่ bridge chip ให้ค่าอุณหภูมิที่ผิด
  (ค่าว่าง, 0, 65535) ทำให้ไอคอนแสดงตัวเลขมั่วหรือค้าง `--`
  รายละเอียดใน [RESEARCH.md](RESEARCH.md)
- แก้บั๊กแฝง: ค่า `Temperature` เป็นสตริงว่างจากอุปกรณ์ USB ทำให้
  `int("")` ยก `ValueError` และ**เธรดอัปเดตล่มเงียบ ๆ** — ไอคอนค้างค่าเดิม
  ตลอดไป ตอนนี้แปลงค่าแบบปลอดภัยแล้ว
- เพิ่ม sanity check อุณหภูมิต้องอยู่ในช่วง -20..100 °C ค่านอกช่วง
  ถือเป็นขยะและแสดงเป็น n/a แทน

### Changed

- `start_ssd_temp_monitor.bat` ใช้ `%~dp0` อ้าง path ของสคริปต์
  แทน path ตายตัว `C:\_Project\ssd_temp_monitor\` — ย้ายโฟลเดอร์/
  ใช้หลายเครื่องได้

### Added

- เอกสารประกอบใน `docs/`: คู่มือผู้ใช้ภาษาไทย, เอกสารวิจัยสาเหตุของบั๊ก,
  changelog (ทั้ง .md และ .html พร้อมรูปแผนภาพ SVG)
- `AGENT.md` — คู่มือสำหรับ AI coding agent

## [1.0.0] — 2026-08-22

### Added

- แสดงอุณหภูมิ SSD ที่ร้อนที่สุดบน system tray icon อัปเดตทุก 1 วินาที
- สีเตือนตามระดับอุณหภูมิ: เขียว ≤ 50 °C, ส้ม 51–64 °C, แดง ≥ 65 °C
- Tooltip แสดงอุณหภูมิทุกดิสก์, หน้าต่าง details, เมนู Refresh / Exit
- Self-elevate ขอสิทธิ์ Administrator ผ่าน UAC อัตโนมัติ
- อ่านค่าผ่าน `Get-PhysicalDisk | Get-StorageReliabilityCounter` (PowerShell)
