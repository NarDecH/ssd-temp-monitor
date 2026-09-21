@echo off
rem Starts the SSD Temperature tray monitor (requests admin via UAC if needed)
rem %~dp0 = folder containing this .bat, so the script location is portable
start "" pyw "%~dp0ssd_temp_tray.py"
