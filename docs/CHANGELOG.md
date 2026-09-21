# Changelog

รูปแบบตาม [Keep a Changelog](https://keepachangelog.com/th/1.1.0/)
เวอร์ชันตาม [SemVer](https://semver.org/lang/th/)

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
