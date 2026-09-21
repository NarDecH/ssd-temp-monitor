# การเซ็นโค้ด (Authenticode Code Signing)

ปัจจุบัน `ssd_temp_monitor.exe` และตัวติดตั้ง**ยังไม่ได้เซ็น** — เมื่อดาวน์โหลด
Windows SmartScreen จะแสดงเตือน "Unknown publisher" ผู้ใช้สามารถกด
**More info → Run anyway** เพื่อติดตั้งได้ตามปกติ

เอกสารนี้อธิบายวิธีเปิดใช้การเซ็นอัตโนมัติใน release workflow

## ตัวเลือกใบรับรอง

| ประเภท | ราคาโดยประมาณ | ผลกับ SmartScreen |
|---|---|---|
| **OV** (Organizational Validation) เช่น Certum Open Source Code Signing, SSL.com | ~$70–250/ปี | เริ่มจาก "Unknown" แล้วสะสม reputation ตามจำนวนดาวน์โหลด |
| **EV** (Extended Validation) เช่น SSL.com EV, DigiCert | ~$250–500/ปี | SmartScreen เชื่อถือเร็วกว่ามาก (hardware token บังคับ) |
| Self-signed | ฟรี | ไม่ช่วย SmartScreen — เหมาะกับใช้ภายในองค์กรเท่านั้น |

หมายเหตุ: มาตรฐาน CA/Browser Forum บังคับให้ key อยู่ใน hardware token
หรือ HSM ตั้งแต่มิถุนายน 2023 — ถ้า provider ให้เลือก cloud signing
(เช่น Azure Trusted Signing, SSL.com eSigner) จะใช้กับ CI ได้ง่ายกว่า

## เปิดใช้ใน release workflow

release.yml มีขั้น **Sign binaries** ที่ทำงานเมื่อตั้งค่า secrets ครบเท่านั้น
(ไม่ตั้ง = ข้ามไปเลย ปลอดภัยกับ repo สาธารณะ)

เพิ่ม secrets ใน GitHub: **Settings → Secrets and variables → Actions →
New repository secret**

| ชื่อ secret | ค่า |
|---|---|
| `SIGNING_PFX_BASE64` | ไฟล์ `.pfx` encode base64 — ดูวิธีด้านล่าง |
| `SIGNING_PFX_PASSWORD` | รหัสผ่านของไฟล์ `.pfx` |
| `SIGNING_TSA_URL` | timestamp server เช่น `http://timestamp.digicert.com` |

แปลง `.pfx` เป็น base64 (PowerShell):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\key.pfx")) | Set-Clipboard
```

จากนั้น push tag `v*` ตามปกติ — ไฟล์ที่แนบใน release จะถูกเซ็นด้วย
SHA-256 + timestamp อัตโนมัติ

## ตรวจสอบไฟล์ที่เซ็นแล้ว

```powershell
Get-AuthenticodeSignature .\ssd_temp_monitor_setup_v1.8.0.exe |
  Select-Object Status, SignerCertificate
```

`Status` ควรเป็น `Valid` — ถ้ามี timestamp ไว้ signature จะยังถูกต้อง
แม้ใบรับรองหมดอายุไปแล้ว

## ทำไมต้อง timestamp

การเซ็นพร้อม `/tr` (RFC 3161 timestamp) ทำให้ signature "ถูกต้อง ณ ตอนเซ็น"
ไม่ผูกกับอายุใบรับรอง — เว้นไม่ใส่ เมื่อ cert หมดอายุทุกไฟล์เก่าจะกลายเป็น
"invalid" ทันที
