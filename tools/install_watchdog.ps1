# Installs the 1-minute crash watchdog as a Windows scheduled task.
# Run ONCE from an elevated PowerShell:
#     powershell -ExecutionPolicy Bypass -File tools\install_watchdog.ps1
# The task runs tools\watchdog.ps1 every minute with highest privileges.
# Remove with:  schtasks /Delete /TN "SSDTempMonitor Watchdog" /F

$taskName = "SSDTempMonitor Watchdog"
$script = Join-Path $PSScriptRoot "watchdog.ps1"
$wd = Get-Command wscript.exe -ErrorAction SilentlyContinue  # unused; keep PS

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument `
    "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings -Force |
    Out-Null
Write-Output "watchdog task '$taskName' installed (1-minute interval)"
