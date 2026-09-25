' SSD Temp Monitor - invisible watchdog launcher.
' The scheduled task used to run powershell.exe directly: PowerShell is a
' CONSOLE-subsystem executable, so Windows creates (and destroys) a console
' window every minute before -WindowStyle Hidden can apply -> the taskbar
' shows a flickering PowerShell window. wscript.exe is a GUI-subsystem
' executable: no console is ever created, and .Run(..., 0, ...) keeps the
' child PowerShell hidden as well. Result: a fully invisible task.
'
' Installed by tools/install_watchdog.ps1 (wscript.exe watchdog_launcher.vbs).

Dim sh, script
Set sh = CreateObject("Wscript.Shell")
script = Replace(WScript.ScriptFullName, "watchdog_launcher.vbs", "watchdog.ps1")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """", 0, False
