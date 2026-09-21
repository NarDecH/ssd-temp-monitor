# AGENT.md — คู่มือสำหรับ AI Agent

แนวทางปฏิบัติสำหรับ AI coding agent (และมนุษย์) ที่ทำงานกับโปรเจกต์นี้

## ภาพรวมโปรเจกต์

**SSD Temperature Tray Monitor (Windows)** — แอป Python ขนาดเล็กไฟล์เดียว
(`ssd_temp_tray.py`) ที่แสดงอุณหภูมิ SSD บน system tray icon ของ Windows
อัปเดตทุก 1 วินาที พร้อมสีเตือน (เขียว/ส้ม/แดง)

โครงสร้างไฟล์:

```
ssd_temp_tray.py               โค้ดหลักทั้งหมด (tray + polling + history + graph + alert + settings)
start_ssd_temp_monitor.bat     ตัวเรียกสคริปต์ (ใช้ %~dp0 เพื่อความพกพา)
make_icon.py                   สร้าง app_icon.ico (Pillow)
build_exe.bat                  build dist/ssd_temp_monitor.exe ด้วย PyInstaller
setup.iss                      สคริปต์ Inno Setup -> installer/ssd_temp_monitor_setup.exe
app_icon.ico                   ไอคอนของแอป/exe
tests/                         pytest suite (รันได้ไม่ต้องมี admin/PowerShell/GUI)
.github/workflows/ci.yml      GitHub Actions: pytest + build exe/installer
README.md                      คู่มือผู้ใช้ (ภาษาอังกฤษ)
docs/                          เอกสาร .md + .html + รูป SVG
dist/ssd_temp_monitor.exe      ตัวแจกจ่าย (build แล้ว)
```

Config ของผู้ใช้อยู่ที่ `%APPDATA%\SSDTempMonitor\config.json`
(สร้างอัตโนมัติเมื่อบันทึก Settings) — ค่าเริ่มต้นดู `DEFAULT_SETTINGS`

## กฎสำคัญที่ต้องรู้ก่อนแก้โค้ด

1. **ห้ามอ่านอุณหภูมิจากดิสก์ที่ BusType เป็น USB** — USB card reader / enclosure
   มักรายงาน `MediaType = 'SSD'` แต่ bridge chip ให้ค่าอุณหภูมิที่ผิด (ค่ามั่ว,
   0, 65535 หรือค่าว่าง) **และวัดจริงพบว่าทำให้การอ่าน
   `Get-StorageReliabilityCounter` ค้างถึง ~42 วินาทีต่อดิสก์** (NVMe ปกติ 0.1 s)
   ดู `read_temps()` และ `docs/RESEARCH.md`
   แบบนี้คือจุดบั๊กเดิมของโปรเจกต์ ห้ามถอยกลับไปใช้ filter
   `MediaType -eq 'SSD'` เฉย ๆ
2. **ต้องกรอง USB ก่อนอ่าน counter เสมอ** — มี query 2 ตัว:
   `PS_TEMPS` (กรอง USB ใน Where-Object ก่อน pipe เข้า counter) สำหรับ tray,
   และ `PS_LIST` (รายชื่อทุกดิสก์ ไม่อ่าน counter เลย) สำหรับหน้า debug
   ห้ามรวมเป็น query เดียวที่อ่าน counter ของทุกดิสก์
2. **ค่าอุณหภูมิต้องผ่าน sanity check** (-20..100 °C) ก่อนแสดงผลทุกครั้ง
   และการแปลง `int()` ต้องกันค่า `None`/`""` เสมอ (ค่าว่างจาก USB ทำ
   `int("")` พังและเธรดอัปเดตตายเงียบ ๆ)
3. **PowerShell ใน subprocess ต้องใส่ `$ErrorActionPreference='SilentlyContinue'`**
   และ `CREATE_NO_WINDOW` เสมอ — ไม่อย่างนั้นจะมีหน้าต่างดำกระพริบทุก 1 วินาที
   และ error จากอุปกรณ์ที่ไม่รองรับทำ JSON parse พัง
4. **JSON จาก PowerShell อาจเป็น dict เดี่ยวเมื่อมีดิสก์เดียว** — ต้อง wrap
   เป็น list ก่อน iterate (ดูโค้ดปัจจุบัน)
5. **UI ต้องไม่บล็อก**: MessageBox / tkinter ใน callback ของ pystray
   จะค้าง tray icon — ทุกอย่างหนัก ๆ ต้องรันใน thread แยก
   (ดู `_details_window` และ `refresh`)
6. **แอปต้องการสิทธิ์ Administrator** เพื่ออ่าน
   `Get-StorageReliabilityCounter` — มีกลไก self-elevate ผ่าน UAC อยู่แล้ว
   อย่าเอาออก
7. **ไฟล์โค้ดหลักไฟล์เดียว** — โปรเจกต์นี้ตั้งใจให้ logic ทั้งหมดอยู่ใน
   `ssd_temp_tray.py` ไฟล์เดียว (`make_icon.py` เป็นเพียงเครื่องมือ build ไอคอน)
8. **เมนู pystray มีขีดจำกัด** — MenuItem ที่เป็น checkbox ต้องใช้ `checked=`
   callback, หน้าต่าง tkinter แต่ละประเภทเปิดได้ครั้งละ 1 ด้วย flag
   (`_detail_open` / `_graph_open` / `_disks_open`) กันเปิดซ้ำจนซ่อนไม่ได้
9. **รอบระยะห่างการบันทึก** — history จำกัด 30 นาที / flush CSV ทุก 60 s
   เพื่อไม่ให้เขียนดิสก์หนักเกิน (นี่คือแอปวัดสุขภาพดิสก์ ไม่ควรไปเพิ่มโหลดดิสก์เอง)
10. **Alert state machine ต้องเป็น pure function** — logic การเตือนอยู่ใน
   `alert_state(hottest, since, last_alert, now)` และอัปเดต state ใน
   `App._alert_drive()` ห้ามแตะ state จากหลายเธรดโดยไม่ล็อก
   (`self._lock` ครอบทั้งการอ่าน-เขียน `_alert_since`/`_last_alert`)
11. **Single instance มีสองชั้น** — แอปใช้ mutex `Local\SSDTempMonitor_SingleInstance`
   (`acquire_single_instance()` ผ่าน `ctypes.WinDLL(..., use_last_error=True)`
   + `ctypes.get_last_error()` — **ห้าม**ใช้ `windll.kernel32.GetLastError()` เพราะ
   ค่าไม่น่าเชื่อถือ) และ installer ใช้ `AppMutex` ชื่อเดียวกันใน `setup.iss`
   — ถ้าจะเปลี่ยนชื่อ ต้องแก้ทั้งสองไฟล์ · duplicate start ออกด้วย exit code 2;
   ระบบอัตโนมัติกด MessageBox ข้ามได้ด้วย `--duplicate-silent` หรือ
   `SSD_TEMP_SILENT_DUPLICATE=1` (**UAC elevation ไม่ส่งต่อ env vars**
   ของ parent — ใช้ flag ผ่าน command line แทน)
12. **กราฟเป็นหน้าต่าง persistent และปิดผ่านธงเท่านั้น** — ทุก tk call
   ต้องเกิดบนเธรดของกราฟเอง (`_tick` loop): การปิดตอน Exit ทำโดยตั้ง
   `App._shutdown_requested` แล้ว**รอ** `App._graph_done` (timeout 3 s)
   **ห้าม**เรียก `.destroy()`/`deiconify()`/`lift()` ข้ามเธรด — นี่คือสาเหตุเดิม
   ของบั๊ก "หน้าต่าง History ปิดไม่ได้/tray ค้าง" เปิดซ้ำให้ขึ้นหน้าต่างเดิม
   ด้วย Win32 (`App._raise_window`) ไม่ใช่ tk
13. **Settings ผ่าน `_validate_settings` เสมอ** — ค่าจากผู้ใช้/config.json
   ต้องผ่าน clamp ก่อนใช้ และบันทึกลง `%APPDATA%\SSDTempMonitor\config.json`
   ด้วย `save_settings`; env `SSD_TEMP_RECORD_HISTORY` มีสิทธิ์เหนือ config
   (ใช้เป็น override ชั่วคราวได้)

## การ build exe และ installer

```bash
pip install pyinstaller pystray pillow
./build_exe.bat        # สร้าง app_icon.ico แล้ว PyInstaller -> dist/ssd_temp_monitor.exe
iscc setup.iss         # สร้าง installer/ssd_temp_monitor_setup.exe (ต้องติดตั้ง Inno Setup 6)
```

- ผลลัพธ์: `dist/ssd_temp_monitor.exe` (onefile, windowed, `--uac-admin`, ฝังไอคอน)
- ต้องมี `--hidden-import pystray._win32` เสมอ ไม่งั้น exe รันแล้วไอคอนไม่ขึ้น
- โค้ดรองรับ `sys.frozen` แล้ว — relaunch_elevated จะ elevate ตัว exe เอง
- Inno Setup อยู่ที่ `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`
  (ติดตั้งผ่าน `winget install -e --id JRSoftware.InnoSetup`)
- `setup.iss` ตั้ง `ArchitecturesInstallIn64BitMode=x64compatible` แล้ว —
  ถ้าไม่ตั้ง จะติดตั้งลง **Program Files (x86)** ทั้งที่เป็น build 64-bit
- ไฟล์ที่สร้างเองจาก build (`build/`, `dist/`, `installer/`, `*.spec`) ไม่ต้องแก้มือ

## การทดสอบ

- **รัน pytest ทุกครั้งที่แก้ logic** — `python -m pytest tests/ -v`
  (114 เคส รันได้โดยไม่ต้องมี admin/PowerShell/GUI; PowerShell ถูก
  monkeypatch ที่ `_run_powershell` เสมอ)
- ตรวจ syntax เพิ่มด้วย `python -m py_compile ssd_temp_tray.py`
- ทดสอบ filter ด้วยคำสั่ง (ต้อง run PowerShell ใน terminal ที่รองรับ
  quoting ให้):

  ```powershell
  Get-PhysicalDisk | Select Model, MediaType, BusType
  ```

  ต้องเห็นว่าดิสก์ USB ถูกกรองออกใน logic ของ `read_temps()`
- ทดสอบบนเครื่องที่มีและไม่มี USB reader เสียบอยู่ — ไอคอนต้องโชว์
  อุณหภูมิ SSD ภายในถูกต้องเสมอ

## การรันคำสั่งด้วยสิทธิ์ admin (elevation)

- **ห้ามใส่ quoted path + arguments ตรง ๆ ในคำสั่งที่ขอ elevation** —
  wrapper รันคำสั่งผ่าน PowerShell ทำให้
  `"C:\path with spaces\setup.exe" /SILENT` ถูกตีความเป็น string
  expression ไม่ใช่คำสั่ง → parse error → exit code 1 โดยโปรแกรมเป้าหมาย
  **ไม่ถูกรันเลย** (สัญญาณ: exit 1 แต่ไม่มี log / ไม่มี output ใด ๆ)
- วิธีที่ถูกต้อง: เขียนสคริปต์ `.ps1` แล้วขอ elevation รัน
  `powershell -NoProfile -ExecutionPolicy Bypass -File <script>`
  ภายในสคริปต์ใช้ `Start-Process -Wait -PassThru` เพื่อได้ ExitCode จริง
  และตรวจ `/LOG=<path>` ของ Inno Setup ได้ทันที
- **UAC ไม่ส่งต่อ environment variables** จาก parent ไปลูก — ส่งสัญญาณ
  ด้วย command-line flag แทน (เช่น `--duplicate-silent` ที่แอปรองรันไว้)
- ตัวติดตั้ง Inno ที่ตั้ง `AppMutex` จะจบด้วย exit code 1 เมื่อแอปยังรัน
  อยู่ — ต้อง `taskkill /IM ssd_temp_monitor.exe /F` (แบบ elevated) ก่อน
  ติดตั้งเวอร์ชันใหม่
- ตรวจความถูกต้องของ exe ที่ติดตั้งได้ด้วย SHA-256 เทียบกับ
  `SHA256SUMS.txt` ใน release (กลไกเดียวกับ updater ใช้)
- **กลไก auto-update ต้องผ่าน AppMutex ให้ได้ก่อน**: installer จะ abort
  (exit 1 เงียบ ๆ) ถ้าแอปยังถือ mutex — โค้ดจึงรันตัวติดตั้งผ่าน cmd shim
  (`build_update_shim`) ที่รอ process ของแอปออกก่อนทุกครั้ง
- **ห้ามพึ่ง PATH ใน batch shim** — ใช้ path เต็ม
  `%SystemRoot%\System32\tasklist.exe` / `find.exe` / `ping.exe` เสมอ:
  เครื่องที่มี Git Bash ใน PATH จะให้ `find` ชี้ไปที่ **GNU find** แล้ว
  pipeline พังเงียบ ๆ (ทดสอบพบจริง)
- เทสแบบ unattended ใช้ `--update-now` (พิมพ์ `UPDATE-RESULT:` ทาง stdout)
  และ `--duplicate-silent` — ทั้งสอง flag มี unit test ครอบ
- **ระวัง shell layer ของเทอร์มินัล**: บางชั้นแปลง `>nul` เป็น `>/dev/null`
  อัตโนมัติแล้ว cmd.exe พัง — ในเทสจึงเรียก `ping` แบบ list ไม่ใช้
  redirection

## สไตล์โค้ด

- Python 3 มาตรฐาน, type hints ในระดับฟังก์ชัน, docstring ภาษาอังกฤษ
- คอมเมนต์/เอกสารผู้ใช้: README ภาษาอังกฤษ, เอกสารใน `docs/` สองภาษา
- หลีกเลี่ยง dependency ใหม่ — ปัจจุบันใช้แค่ `pystray`, `Pillow`
  (และ standard library)

## เอกสารที่ต้องอัปเดตเมื่อแก้พฤติกรรม

| ไฟล์ | เมื่อไร |
|---|---|
| `README.md` | เปลี่ยนวิธีใช้ / requirement |
| `docs/RESEARCH.md` + `.html` | เปลี่ยนวิธีอ่านค่าอุณหภูมิ |
| `docs/CHANGELOG.md` + `.html` | ทุกครั้งที่แก้โค้ดที่ user เห็นผล |
| `tests/` | เพิ่มเคสทุกครั้งที่เพิ่ม/แก้ logic ที่ทดสอบได้ |
| `build_exe.bat` + build ใหม่ | แก้โค้ดที่ไปอยู่ใน exe |
