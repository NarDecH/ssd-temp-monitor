# Installs the 1-minute crash watchdog for a developer/manual setup.
# The Inno Setup installer does this automatically (see setup.iss) - run this
# script only for portable/manual layouts.
#
# What it does:
#   1. copies watchdog.ps1 + watchdog_launcher.vbs into the app dir
#      (<app dir>\watchdog\), so the scheduled task does not depend on this
#      repository checkout staying in place
#   2. registers the task via tools\register_watchdog_task.ps1
#
# Remove with:  schtasks /Delete /TN "SSDTempMonitor Watchdog" /F

$ErrorActionPreference = "Stop"

$appDir = "C:\Program Files\SSD Temp Monitor"
$appExe = Join-Path $appDir "ssd_temp_monitor.exe"
$dest = Join-Path $appDir "watchdog"

if (-not (Test-Path $appExe)) {
    Write-Warning "app not found: $appExe - install the app first, then run this script"
    exit 1
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot "watchdog.ps1") $dest
Copy-Item -Force (Join-Path $PSScriptRoot "watchdog_launcher.vbs") $dest

& (Join-Path $PSScriptRoot "register_watchdog_task.ps1") -AppDir $appDir
