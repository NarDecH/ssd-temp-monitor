# Installs the 1-minute crash watchdog as a Windows scheduled task.
# Run ONCE from an elevated PowerShell:
#     powershell -ExecutionPolicy Bypass -File tools\install_watchdog.ps1
# The task runs tools\watchdog.ps1 every minute with highest privileges,
# in the LOGGED-ON USER's session: a tray app started by a SYSTEM task
# would run in session 0 where the icon is invisible (v1.24.4 bug).
# -LogonType Interactive: only runs while the user is logged on, which is
# exactly right for a tray app. The app's UAC manifest still elevates it.
#
# The task launches wscript.exe (GUI subsystem) with watchdog_launcher.vbs
# instead of powershell.exe (console subsystem) directly: PowerShell's
# console window is created before -WindowStyle Hidden applies, so the old
# action flashed a PowerShell window on the taskbar every minute. wscript
# creates no console at all and the VBS runs the same script fully hidden.
# Remove with:  schtasks /Delete /TN "SSDTempMonitor Watchdog" /F

$taskName = "SSDTempMonitor Watchdog"
$launcher = Join-Path $PSScriptRoot "watchdog_launcher.vbs"

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
Write-Output "watchdog task '$taskName' installed (1-minute, interactive session, invisible via wscript)"
