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
  (212 เคส รันได้โดยไม่ต้องมี admin/PowerShell/GUI; PowerShell ถูก
  monkeypatch ที่ `_run_powershell` เสมอ)
- **เวอร์ชัน pre-release** — `_parse_version` เรียง `rc1 < rc2 < release`
  (มี 5 ชั้น: core + flag + rc number) ห้ามกลับไปตัด suffix แบบเดิม
  (ทำให้ช่องทาง pre-release ใช้ไม่ได้ — เคยเป็นบั๊กใน ≤1.13.x)
- **เพิ่มภาษาใหม่** — copy dict ใน STRINGS, แปลให้ครบทุกคีย์, เพิ่มใน
  UI_LANGUAGES + เมนู tray — เทส parity จะจับถ้าลืม
- **การเทสพิกเซลของไอคอน** — ต้องกรอง `alpha > 0` ก่อนเสมอ (มุมโค้งนอก
  เม็ดเป็น (0,0,0,0) ที่ดูเหมือนเลขดำได้) และระวังว่าฟอนต์ถูกย่อให้เต็ม
  ความกว้างเม็ดแล้ว จึงเหลือ slack แนวตั้งมากกว่าแนวนอน (clamp ทำงานจริง
  เฉพาะเมื่อมี slack — เทสขยับใช้ 128 px)
- **UI ทุกข้อความต้องผ่าน `tr()` เท่านั้น** — ห้าม hardcode ข้อความภาษา
  อังกฤษ/ไทยในเมนู/หน้าต่าง/การแจ้งเตือน เพิ่ม key ใหม่ใน `STRINGS`
  ทั้งสองภาษาพร้อมกัน (เทส `TestI18n::test_strings_parity` จับถ้าลืม)
  ส่วนขยายภาษาอื่น: เพิ่ม dict ใน `STRINGS` + ปรับ `_validate_settings`
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
- **`--version-file` ของ PyInstaller ไม่ใช่ไฟล์ .rc** — มัน parse เป็น
  Python literal ของ `VSVersionInfo` (ดู `make_version_file.py`); .rc
  มาตรฐานจะ deserialize ไม่ได้
- **`run: |` ใน GitHub Actions (pwsh) ไม่ต่อบรรทัดให้** — คำสั่งยาว ๆ
  เช่น PyInstaller ต้องอยู่บรรทัดเดียว หรือใช้ backtick ต่อท้ายบรรทัด
  (เครื่องหมาย `--` ขึ้นต้นบรรทัด = unary operator error)
- เวอร์ชันของแอปอ่านจาก VERSIONINFO ของ exe จริงผ่าน `effective_version()`
  — ห้าม hardcode ตัวเลขเทียบเอง; การตัดสิน "เวอร์ชันต่างกัน" ที่เชื่อถือได้
  คือ hash เท่านั้น (timestamp ของไฟล์ข้าม build อาจตรงกันเป๊ะ)

## บั๊กที่เคยเจอ (ต้องไม่กลับมา)

- **เปิด Settings ไม่ได้ (v1.11.0)** — `preview_lbl.image = preview_lbl.image`
  อ่านค่าชื่อ image เดิมกลับใส่ตัวเอง ทำให้ PhotoImage ใหม่ไม่มีใครอ้างอิง
  → GC destroy ภาพ → หน้าต่างวาดไม่ได้ กฎ: **ต้องเก็บ PhotoImage ในตัวแปร
  Python จริงเสมอ** (`photo = tk.PhotoImage(...); lbl.config(image=photo);
  lbl.image = photo`) และ preview ต้อง wrap ด้วย try/except เพื่อไม่ให้
  หน้าต่าง Settings พังทั้งหน้าตามไปด้วย
- **Dialog "Security validation failure: invalid originating onefile
  parent process (PID not found)"** — PyInstaller 6.22 (GHSA-9fxf-4qw3-ghmr)
  ให้ onefile child ตรวจว่า parent ยังมีชีวิตอยู่ ซึ่งเคยขัดกับ flow
  อัปเดตของเรา (updater/cmd ตายก่อนแอป relaunch) แก้ด้วย
  (1) shim relaunch ผ่าน `explorer.exe` (แอปใหม่ไม่มี parent ที่จะตาย)
  (2) ตั้งแต่ 6.22.3 ทีม PyInstaller แก้ guard แล้ว — **พิสูจน์ด้วยโพรบ
  onefile จำลอง 3 เคส (parent ตายก่อน boot / parent มีชีวิตตลอด /
  explorer-relaunch) บน 6.22.3 ผ่านหมด** จึง unpin เป็น
  `pyinstaller>=6.22.3,<7` ทุกจุด บทเรียน: pin ที่เกิดจากบั๊ก upstream
  ควรถูกทดสอบคลายเมื่อ upstream ออก patch อย่าปล่อย pin ถาวรโดยไม่พิสูจน์ซ้ำ
- **อย่าสมมติ API ของไลบรารีภายนอกโดยไม่ verify** — ฟีเจอร์เก็บกวาด `_MEI`
  (v1.13.0) รอบแรกเขียนอ้าง marker file `PYINSTAINER_ONFILE_PARENT` ที่
  **ไม่มีอยู่จริงใน PyInstaller** (ค้นเว็บยืนยัน) ก่อนเผยแพร่ต้องเปลี่ยนเป็น
  เทคนิคที่พึ่งพฤติกรรม OS ล้วน ๆ: โฟลเดอร์ที่ DLL ถูก map โดยโปรเซสมีชีวิต
  จะ **rename ไม่ได้** (sharing violation) → rename สำเร็จ = เจ้าของตายแล้ว
  จึงลบได้ปลอดภัย (รวมถึงต้องเว้นโฟลเดอร์ที่เพิ่งสร้าง < 60 วิ และ
  `sys._MEIPASS` ของตัวเอง) เวลาต้องการ "ตรวจว่าโปรเซสยังใช้ไฟล์อยู่ไหม"
  ให้คิดที่ file-locking ของ OS ก่อนเสมอ ไม่ใช่ marker ที่เราสมมติ
- **ทดสอบเทียบกับ semantic จริงของโค้ด ไม่ใช่ความจำ** — self-test รอบแรก
  ใช้สมมติฐานผิด 3 จุด: `_parse_version("1.9.0-rc1")` ตัด suffix ทิ้ง
  (จึงไม่ใหม่กว่า 1.9.0), `select_release_asset` ต้องมี `state: uploaded`
  ใน asset และ version ที่คืนเป็น tag เต็มพร้อม `v` — เขียนเทส/self-test
  ทีไร ให้รัน probe ยืนยันพฤติกรรมจริงก่อนเขียน assertion
- **PowerShell: property ไม่ใช่คำสั่ง (v1.14.0)** — เขียน
  `$c.Prop|ReadErrorsTotal` (pipeline ไปยังชื่อ property) ทำให้ PS ตีความ
  `ReadErrorsTotal` เป็นคำสั่ง → statement พังทุกรอบ loop → stdout ว่างหมด
  แม้ `$ErrorActionPreference='SilentlyContinue'` จะซ่อน error ก็ตาม
  (**stdout ว่าง + stderr เงียบ ≠ query ถูก** — ต้องจับ stderr ด้วยเมื่อ probe)
  แก้เป็น property access ตรง ๆ `$c.ReadErrorsTotal` บทเรียน: query PS ทุก
  ตัวที่แก้ ต้องรันจริงและอ่าน stderr ก่อนเผยแพร่เสมอ
- **rollback ต้องกันวงจรอัปเดตซ้ำ** — คืนเวอร์ชันเก่าแล้วแต่ไม่จำเวอร์ชันที่พัง
  = auto-update จะเสนอเวอร์ชันเดิมใหม่ทันที → วน rollback ไม่รู้จบ
  จึงมี `update_broken_versions.txt` (updater ข้าม tag ที่เคย rollback)
- **เทสที่เขียนไฟล์ "ดำ (blacklist)" = ปนเปื้อนระดับ production** — เทส
  `remember_broken_version` ที่ไม่ isolate ทำให้เครื่อง dev บันทึก `v1.15.0`
  ลงไฟล์จริง → ถ้าปล่อยไว้ ตัวอัปเดตจะ**ข้าม release v1.15.0 ตัวจริงตลอดไป**
  กฎ: ทุกฟังก์ชันที่เขียน state ถาวรของแอป ต้องมีเทส monkeypatch path และ
  ตรวจด้วย `stat` ขนาดไฟล์จริงก่อน/หลังรันเทส อย่าเชื่อว่า conftest จับครบ
  (โพรบ standalone ที่รัน `python -c` นอก pytest ไม่ผ่าน conftest เสมอ)
- **E2E ของกลไก update/rollback ต้องแยก 3 ชั้น** — (1) เปลี่ยนชื่อ exe
  sandbox ให้ต่างจากแอปจริงเพื่อไม่ให้ `IMAGENAME eq` ใน shim ไป match แอป
  ที่รันอยู่จริง (2) รัน shim แบบ redirect ลงไฟล์ **ห้าม**
  `capture_output=True` — shim spawn `explorer.exe` ที่ไม่มีวันตายและรับ
  stdio ไปด้วย → pipe write-end ไม่ปิด → `communicate()` บล็อกตลอดกาล
  (production ไม่มีปัญหาเพราะใช้ close_fds ไม่ capture) (3) ห้ามใช้
  `timeout.exe` เป็น sleep ในสคริปต์ที่ stdin ไม่ใช่ console (มันปฏิเสธ
  ทำงาน) — ใช้ `ping -n N` และ E2E ต้อง measure log delta = 0 ทุกรอบ
- **Smart counters อ่านได้ null ครั้งแรกหลังเปิด session ไม่ใช่บั๊ก** —
  Storage cache ยังไม่เติม (`Update-HostStorageCache` ช่วยได้) การ poll
  ทุกวินาทีของแอปครอบคลุมอยู่แล้ว อย่ารีบ "แก้" query เมื่อโพรบครั้งแรก
  ว่างเปล่า — รันซ้ำก่อนสรุป
- **คำสั่งยกระดับสิทธิ์ผ่าน wrapper อีกชั้นจะกิน `$`, `\` และแตกบรรทัดยาว** —
  เมื่อต้องรัน PowerShell ที่ซับซ้อนแบบ admin ให้ส่ง `-EncodedCommand`
  (base64 utf-16-le, ไม่มีช่องว่าง) และระวัง `Set-Content -Append` ไม่มีใน
  PS 5.1 — สะสมผลใน array แล้วเขียนครั้งเดียว และแอปอาจต้องแยก admin
  probe เป็น script แยกที่ความยาวพอเหมาะ
- **ไฟล์ข้อมูลต้องอยู่ใต้ DATA_DIR ทั้งหมด (portable ต้องยกไปได้ทั้งโฟลเดอร์)** —
  HISTORY_FILE เคยอยู่ที่ %TEMP% จน v1.17.0 กว่าจะเจอ เพิ่มไฟล์ state ใหม่ทีไร
  ให้เช็คเสมอว่าอ้าง DATA_DIR ไม่ใช่ TEMP/cwd และเขียนเทส assert
  `dirname == DATA_DIR` กันย้อน
- **ฟีเจอร์ตรวจจับ "การเปลี่ยนแปลง" ต้องตั้ง baseline เงียบ ๆ ก่อน** —
  watchdog SMART รอบแรกที่เห็นดิสก์ต้องจด state โดยไม่เตือน (ดิสก์ที่มี
  error ค้างจากซื้อมาจะโดน alert storm ทันทีตอนติดตั้งแอปครั้งแรก)
  และ state ต้อง persist — ไม่งั้นทุกครั้งที่รีสตาร์ทจะเตือนซ้ำสิ่งเดิม
- **marker ของกลไก update ต้องมีอายุ ไม่ใช่แค่มีอยู่** — คืน release 1.15.0
  จริง: REPORTED marker ค้างจากรอบเก่า (เทส) ทำให้แอปใหม่ที่บูตสำเร็จ
  blacklist ตัวเอง + เด้ง dialog ผิด (`_notify_rollback_reported` กิน
  PENDING ของการอัปเดตปัจจุบันไปด้วย) กฎ: marker ที่ใช้ตัดสิน "เกิดขึ้นเมื่อ
  ไม่นานนี้" ต้องเทียบ mtime กับขอบเขตเวลาเสมอ และเทสต้องครอบทั้ง marker
  สดและ marker เก่า (ผ่าน `os.utime`)
- **autostart จากซอร์สจะจด pythonw ลง Run key** — รันแอปจากซอร์สแล้วเปิด
  checkbox autostart ทำให้เครื่องบูตมาเปิดแอปผ่าน `pythonw` ตลอด
  (exe ตัวจริงจะโดน mutex บล็อกตอน self-update → exit 2 แบบเงียบ ๆ)
  วินิจฉัยได้ด้วย `Get-Process pythonw*` + โพรบ mutex ตรง ๆ และแก้ Run key
  ให้ชี้ exe ที่ติดตั้งก่อนอัปเดต (ต้องคิดถึงกรณี `sys.frozen=False`
  ใน `set_autostart`)
- **งานที่ถูกขัดจังหวะต้อง `py_compile` + pytest ครบก่อนแตะงานใหม่** —
  รอบ v1.18.0 การแก้ที่ถูก Stop ค้างเศษไว้: ตัวแปร `e` ที่ไม่มีอยู่จริง
  (NameError) และ `start` ที่คำนวณแล้วทิ้ง — pytest บนเครื่องจับได้ทันก่อน
  commit แต่ถ้าปล่อยผ่าน CI ไปก่อนคือ release พังอีก กฎ: เปิดงานต่อจาก
  turn ที่ถูกขัดเสมอ ให้รันชุดทดสอบเต็มก่อนเขียนโค้ดใหม่แม้แต่บรรทัดเดียว
- **เปลี่ยน DEFAULT_SETTINGS = เทสที่ assert ค่าเริ่มต้นพังตามทันที** —
  รอบ v1.19.0 เปลี่ยนค่าเริ่มต้นตัวเลข (ขาว/regular/Arial) แล้วเทสพิกเซล 2 ตัว
  พังเพราะเดิมพึ่ง auto-contrast (เลขดำบนเม็ดเขียว) ค่าเริ่มต้นใหม่ (เลขขาว)
  ทำให้ "dark pixel" ที่เทสใช้หาไม่เจอ กฎ: เทสพิกเซลต้องตั้งค่าที่ต้องการ
  อย่าพึ่งค่า default ของ SETTINGS และเปลี่ยน default ทีไร ให้ grep เทส
  ที่ assert ค่า default เดิมด้วย
- **silent uninstall ใน CI ต้อง /FORCECLOSEAPPLICATIONS + poll ไฟล์จริง** —
  unins000 แบบ /VERYSILENT บางครั้งคืน 0 แต่ exe ยังอยู่ชั่วขณะ (lock/
  instance หลงเหลือ) ทำให้ job พังแบบ flaky (เจอครั้งแรก v1.19.0) กฎ:
  อย่าเช็คไฟล์ด้วย fixed sleep — วนรอจนไฟล์หายจริง และบังคับปิดแอปที่ค้าง
  ด้วย /FORCECLOSEAPPLICATIONS ตั้งแต่คำสั่งเดียว
- **สี hex ใน tk ต้องผ่านพาเลตต์เท่านั้น** — ครั้งหนึ่งหน้าต่างแอปเป็นมืดตายตัว
  เพราะ hardcode `#0f172a` กระจายหลายจุด (v1.20.0 รวมเป็น UI_DARK/
  UI_LIGHT + `ui_palette()` ตามธีม Windows) เพิ่ม UI ใหม่ทีไร ให้ดึงสีจาก
  `c = ui_palette()` ห้าม hardcode และแท็บ/หน้าต่างใหม่ต้องมีคีย์ i18n ครบ
  4 ภาษา (เทส parity จับ) รวมถึง fixture `app` ของเทสต้องอัปเดต attribute
  ใหม่ที่ `__init__` สร้าง เพื่อไม่ให้เทสเก่า AttributeError แบบเงียบ ๆ
- **exception ใน menu callback = แอปดับทั้งตัว** — pystray (Win32 backend)
  เรียก callback ตรงใน message loop: exception ที่หลุดทำให้ `icon.run()`
  คืนและโปรเซสจบเงียบ ๆ ไม่มี traceback ใน log ใดเลย (v1.21.0: AttributeError
  จาก flag ที่ไม่ถูกประกาศใน `__init__`) กฎ: (1) `_build_menu` ครอบทุก callback
  ด้วย `_safe()` (2) `_spawn_once` ใช้ `getattr(..., False)` (3) ห้ามเรียก
  `root.after` จาก worker thread — ใช้ marshal ผ่าน `<<SsdMarshal>>`
  (`App.tk_after`) ไม่งั้น Tcl panic ใน tcl86t.dll (0x80000003 ใน Event Log)
  (4) ไฟล์ข้อมูลสำคัญต้อง persist ทันทีที่มีข้อมูลใหม่ อย่ารอ quit —
  crash ทีหลังคือข้อมูลหายตลอด (v1.22.0: ไฟล์ 24h ว่างเพราะเขียนเฉพาะตอน quit)
- **Tcl ห้ามถูกแตะจากเธรดอื่นแม้แต่บรรทัดเดียว — event_generate ก็ไม่ได้** —
  v1.22.0 ยังแตกเพราะ `tk_after` เรียก `event_generate` ข้ามเธรด: บางที raise
  กลับเป็น Python exception (callback หาย → About ค้าง "checking…") บางที
  panic ใน tcl86t.dll เป็น native crash (0x80000003) ที่จับด้วย try/except
  ไม่ได้ — ยิ่งเปิดสองหน้าต่างพร้อมกัน (About + Settings) ยิ่งชนง่าย เพราะ
  Tcl interpreter มีต่อเธรด กฎ: marshal = เข้า `queue.Queue` ล้วน ๆ + drain
  loop บนเธรด tk เอง (จะวนซ้ำก็ใช้ `win.after` บนเธรดตัวเอง) และเช็ค
  "หน้าต่างยังอยู่ไหม" จากเธรดอื่นด้วย flag (`root_winfo_exists`)  ไม่ใช่
  `winfo_exists` (v1.23.0) เสริมได้อีกชั้น: watchdog รอบ `icon.run()`
  (`_watch_icon_loop`) ให้ tray ฟื้นจาก native crash ที่ปิด message loop
- **`icon.run()` ต้องมีเจ้าของเดียวเท่านั้น** — v1.23.1: เพิ่ม watchdog
  แล้วลืมว่า `App.run()` ก็รัน `icon.run()` อยู่แล้ว → message pump สองตัว
  บน tray เดียว ทำให้ tray เสถียรไม่ได้เลย (mainloop ของ Win32 รันสองเธรด
  ไม่ได้) กฎ: ก่อนเพิ่มเลเยอร์ครอบ lifecycle ให้นับว่า message loop ถูก
  start กี่ครั้ง และเขียนเทสอ่านซอร์ส `main()` กันการเรียกซ้ำ
- **"ล้างข้อมูล" ที่อ่านจาก log ต้องคิดถึง 3 ชั้นเสมอ** — v1.24.0: Reset
  statistics เคยลบ log ไม่ได้เลยเพราะ (1) RotatingFileHandler ถือ handle
  เปิด → Windows ห้ามลบไฟล์ที่เปิดอยู่ (PermissionError ถูก except กลืน)
  (2) ตัวนับอ่านจากไฟล์ rotated .1/.2 ด้วย (3) log เองถูกสร้างใหม่ด้วย
  marker line หลังลบ — กฎ: ก่อนลบไฟล์ที่ตัวเองเปิดค้าง ต้อง close/detach
  handler ก่อน (`_reopen_log_handler`) และเทสต้อง assert "ตัวนับ = 0"
  ไม่ใช่ "ไฟล์หาย" เพราะไฟล์อาจถูกสร้างใหม่ทันที
- **pystray menu ต้อง static text เท่านั้น + แอป tray ต้องมี crash log** —
  v1.24.1: Lite ใช้ lambda dynamic text + action=None ใน MenuItem แล้ว
  โปรเซสหายใน ~25 วิ (เมนู render ทุกครั้งที่เปิด และ exception ที่หลุด
  จาก icon.run() ฆ่าแอปทันที) และตรวจยากเพราะ --windowed ตัด console —
  กฎ: MenuItem ใช้ string ธรรมดา, เขียน crash log ลง %TEMP% ตั้งแต่
  main() เริ่ม, และตรวจ sanity ด้วย "สตาร์ท → รอ → เช็ค process ยังอยู่"
  เสมอ (ALIVE probe จับได้ก่อนผู้ใช้รายงาน)
- **ห้ามวางสิ่งที่บล็อกบนเธรดเมนูของ pystray — แม้แต่ MessageBox** —
  v1.24.2: Lite เรียก MessageBoxW ตรงใน menu callback → modal box บล็อก
  message loop → tray ค้าง + กล่องปิดไม่ได้ (เกิดซ้ำกับบั๊ก v1.14 ของแอป
  หลักที่เคยแก้ด้วย _spawn_once) กฎเดียวกันกับ tk: ทุกอย่างที่บล็อก
  (modal box, subprocess, sleep) ต้องไปเธรดของตัวเอง + มีธงกันเปิดซ้อน
  และทดสอบด้วย "เปิดกล่อง → ปิดด้วย WM_CLOSE → เช็ค main thread ยังตอบสนอง"
- **scheduled task สำหรับแอป tray ต้องเป็น interactive session เท่านั้น** —
  v1.24.5: watchdog task แบบ SYSTEM เปิดแอปใน session 0 → tray icon
  มองไม่เห็น แม้โปรเซสยังรัน (เช็คจาก `tasklist` คอลัมน์ Session =
  `Services`)  กฎ: task ที่สตาร์ท GUI ต้องใช้ `LogonType Interactive`
  กับ user ที่ล็อกอิน และหลังติดตั้ง task ต้องเช็ค Session ของโปรเซส
  ที่ task เปิดด้วย ไม่ใช่แค่ "โปรเซสมีอยู่"
- **task ที่รันถี่ (ทุกนาที) ต้องไม่ execute console-subsystem exe ตรง ๆ** —
  v1.24.5: watchdog task รัน `powershell.exe -WindowStyle Hidden` แต่
  PowerShell เป็น console app → Windows สร้าง+ปิดหน้าต่าง conhost ก่อน
  flag ทำงาน → หน้าต่าง PowerShell กระพริบทุกนาที (ผู้ใช้รายงานว่ารบกวน)
  แก้: ให้ task รัน `wscript.exe` (GUI subsystem — ไม่มี console เกิดเลย)
  + VBS launcher (`sh.Run ..., 0, False`) เรียก PowerShell แบบซ่อน
  กฎ: scheduled task ที่รันประจำควร launch ผ่าน GUI-subsystem host เสมอ
- **โปรเซสที่ scheduled task เปิดตกอยู่ใน job object ของ task** — v1.24.5:
  watchdog ใช้ Start-Process เปิดแอป → แอป join job ของ task → task สถานะ
  "Running" ตลอด (trigger ถัดไปโดน MultipleInstances IgnoreNew = error
  0x800710E0) และ ExecutionTimeLimit (5 นาที) ฆ่าแอปที่เพิ่ง restart →
  วงจร kill/restart เงียบ ๆ ทุก 5 นาที แก้: spawn ผ่าน WMI
  (`Invoke-CimMethod Win32_Process Create`) → parent กลายเป็น WmiPrvSE
  ไม่อยู่ใน job  กฎ: task ที่เปิดโปรเซสระยะยาวต้อง spawn นอก job (WMI)
  และหลังติดตั้งต้องเช็คว่า task กลับมา "Ready / Last Result 0" ไม่ใช่แค่
  "แอปถูกเปิด"
- **script ที่รันเงียบ ๆ เป็นประจำต้อง log "ทำไมไม่ทำอะไร"** — E2E watchdog
  รอบแรกล้ม (kill แอปแล้วไม่มีใครปลุก) ทั้งที่ task tick ปกติ (Last Result 0)
  แต่ watchdog.ps1 จบที่ guard ไหน หรือ WMI spawn fail (rc ถูก `| Out-Null`
  ทิ้ง) ไม่มีทางรู้เลย แก้: decision log (`watchdog.log` ใน APPDATA)
  บันทึกเฉพาะเหตุการณ์ผิดปกติ — แอปหาย, guard ข้าม (marker/update shim),
  ผล spawn rc; นาทีที่แอปรันปกติเงียบ ทุกบรรทัดใน log จึงมีความหมาย
  และ tools\e2e_watchdog_test.ps1 (clone task ชื่อทดสอบ ไม่แตะ task จริง)
  ใช้เป็น gate ก่อน release ทุกครั้ง

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
