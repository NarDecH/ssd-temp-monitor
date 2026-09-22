@echo off
rem Rebuild dist\ssd_temp_monitor.exe
rem Requires: pip install "pyinstaller>=6.22.3,<7" pillow pystray
rem (pinned <6.22: newer bootloaders add onefile parent-process validation
rem  that breaks the update flow's detached relaunch)
python make_icon.py
python make_version_file.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name ssd_temp_monitor --icon app_icon.ico ^
  --version-file ssd_temp_monitor_version.txt ^
  --hidden-import pystray._win32 ssd_temp_tray.py
echo.
echo Done: dist\ssd_temp_monitor.exe
echo (optional) build installer:  iscc setup.iss
