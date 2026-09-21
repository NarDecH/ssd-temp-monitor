"""Generate a Windows VERSIONINFO resource for PyInstaller.

Writes ssd_temp_monitor_version.rc so the packaged exe carries a real
version in its file properties. The version is kept in sync with
APP_VERSION automatically at build time.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "ssd_temp_tray.py"
OUT = ROOT / "ssd_temp_monitor_version.rc"


def app_version():
    text = SRC.read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("APP_VERSION not found in ssd_temp_tray.py")
    return m.group(1)


def main():
    version = app_version()
    # FILEVERSION must be purely numeric: "1.9.0-rc1" -> 1,9,0,0
    core = version.split("-")[0]
    numbers = core.split(".")
    while len(numbers) < 4:
        numbers.append("0")
    numeric = ",".join(numbers)
    build_date = "2026-09-22"

    rc = """#include <winver.h>

1 VERSIONINFO
FILEVERSION     {numeric}
PRODUCTVERSION  {numeric}
FILEFLAGSMASK   0x3fL
FILEFLAGS       0x0L
FILEOS          VOS_NT_WINDOWS32
FILETYPE        VFT_APP
FILESUBTYPE     0x0L
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        BLOCK "040904B0"
        BEGIN
            VALUE "CompanyName",      "SSD Temp Monitor Project"
            VALUE "FileDescription",  "SSD Temperature Tray Monitor for Windows"
            VALUE "FileVersion",      "{version}"
            VALUE "InternalName",     "ssd_temp_monitor"
            VALUE "OriginalFilename", "ssd_temp_monitor.exe"
            VALUE "ProductName",      "SSD Temperature Monitor"
            VALUE "ProductVersion",   "{version}"
            VALUE "BuildDate",        "{build_date}"
        END
    END
    BLOCK "VarFileInfo"
    BEGIN
        VALUE "Translation", 0x409, 1200
    END
END
""".format(numeric=numeric, version=version, build_date=build_date)

    OUT.write_text(rc, encoding="ascii")
    print(f"wrote {OUT.name} for version {version}")


if __name__ == "__main__":
    main()
