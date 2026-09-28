# Weekly on-machine stability check (complements the GitHub-side daily
# stability.yml workflow). Runs the same criteria as
# tools/stability_report.ps1 over the last N hours (default 168 = 7 days)
# and appends one summary line to:
#     %LOCALAPPDATA%\SSDTempMonitor\weekly_stability_summary.txt
#
# Intended to be registered as a scheduled task, e.g.:
#     schtasks /Create /F /TN "SSDTempMonitor Weekly Stability" `
#         /SC WEEKLY /D MON /ST 10:55 `
#         /TR "powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\tools\weekly_stability_check.ps1"
#
# Exit codes follow stability_report.ps1: 0 = ALL GREEN, 1 = findings.

param(
    [int]$Hours = 168
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$report = Join-Path $repoRoot "tools\stability_report.ps1"
if (-not (Test-Path $report)) {
    Write-Error "stability_report.ps1 not found next to this script: $report"
    exit 1
}

$output = & powershell -NoProfile -ExecutionPolicy Bypass -File $report -Hours $Hours
$exitCode = $LASTEXITCODE

$lastLine = ($output | Select-Object -Last 1)
$summaryDir = Join-Path $env:LOCALAPPDATA "SSDTempMonitor"
if (-not (Test-Path $summaryDir)) {
    New-Item -ItemType Directory -Path $summaryDir -Force | Out-Null
}
$summaryFile = Join-Path $summaryDir "weekly_stability_summary.txt"
$stamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
$line = "$stamp hours=$Hours exit=$exitCode :: $lastLine"
Add-Content -Path $summaryFile -Value $line -Encoding UTF8

Write-Output $output
Write-Output "summary appended: $summaryFile"
exit $exitCode
