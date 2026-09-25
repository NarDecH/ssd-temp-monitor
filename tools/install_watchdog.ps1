# Installs the 1-minute crash watchdog as a Windows scheduled task.
# Run ONCE from an elevated PowerShell:
#     powershell -ExecutionPolicy Bypass -File tools\install_watchdog.ps1
#
# The launcher + script are COPIED into the app's install dir
# ("C:\Program Files\SSD Temp Monitor\watchdog") so the scheduled task does
# not depend on this repository checkout staying in place - the task keeps
# working after the repo is moved or deleted. watchdog.ps1 already restarts
# "C:\Program Files\SSD Temp Monitor\ssd_temp_monitor.exe", so everything the
# watchdog needs now lives in the install dir.
#
# The task runs with highest privileges, in the LOGGED-ON USER's session:
# a tray app started by a SYSTEM task would run in session 0 where the icon
# is invisible (v1.24.4 bug). -LogonType Interactive: only runs while the
# user is logged on, which is exactly right for a tray app. The app's UAC
# manifest still elevates it.
#
# The task action runs wscript.exe (GUI subsystem) with watchdog_launcher.vbs
# instead of powershell.exe (console subsystem) directly: PowerShell's
# console window is created before -WindowStyle Hidden applies, so the old
# action flashed a PowerShell window on the taskbar every minute. wscript
# creates no console at all and the VBS runs the same script fully hidden.
# Remove with:  schtasks /Delete /TN "SSDTempMonitor Watchdog" /F

$taskName = "SSDTempMonitor Watchdog"
$dest = "C:\Program Files\SSD Temp Monitor\watchdog"
$appExe = "C:\Program Files\SSD Temp Monitor\ssd_temp_monitor.exe"

if (-not (Test-Path $appExe)) {
    Write-Warning "app not found: $appExe - install the app first, then run this script"
    exit 1
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot "watchdog.ps1") $dest
Copy-Item -Force (Join-Path $PSScriptRoot "watchdog_launcher.vbs") $dest

$launcher = Join-Path $dest "watchdog_launcher.vbs"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument `
    "`"$launcher`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings -Force |
    Out-Null
Write-Output "watchdog task '$taskName' installed (1-minute, interactive session, launcher: $launcher)"
