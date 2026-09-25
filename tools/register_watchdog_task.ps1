# Registers (creates or replaces) the "SSDTempMonitor Watchdog" scheduled
# task. Assumes the watchdog files are already in place next to the installed
# app:
#     <app dir>\watchdog\watchdog.ps1
#     <app dir>\watchdog\watchdog_launcher.vbs
# Called by the Inno Setup installer ([Run] section, after files are copied)
# and by tools\install_watchdog.ps1 (developer/manual installs).
#
# Task design (see AGENT.md lessons):
# - wscript.exe (GUI subsystem) runs the VBS launcher: no console window is
#   ever created (a powershell.exe action flashes a window every minute)
# - LogonType Interactive + RunLevel Highest: the tray app must start in the
#   logged-on user's session, not session 0
# - Hidden task, 1-minute repetition, ExecutionTimeLimit 5 min

param(
    [string]$AppDir = "C:\Program Files\SSD Temp Monitor"
)

$ErrorActionPreference = "Stop"

$watchdogDir = Join-Path $AppDir "watchdog"
$launcher = Join-Path $watchdogDir "watchdog_launcher.vbs"
$script = Join-Path $watchdogDir "watchdog.ps1"

if (-not (Test-Path $launcher) -or -not (Test-Path $script)) {
    Write-Error "watchdog files missing in $watchdogDir - run install_watchdog.ps1 or reinstall the app"
    exit 1
}

$taskName = "SSDTempMonitor Watchdog"
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
Write-Output "watchdog task '$taskName' registered (1-minute, interactive session, launcher: $launcher)"
