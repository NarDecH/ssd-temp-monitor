# SSD Temp Monitor - end-to-end test for the crash watchdog.
# Exercises the REAL chain on this machine - run it before every release:
#   case 1  kill the app            -> a watchdog task restarts it
#   case 2  write the quit marker   -> the watchdog task stays silent
# It never disables the real task: it registers a CLONE of it
# ("SSDTempMonitor Watchdog E2E", same action/principal/settings) so the
# real protection keeps running the whole time - either task restarting
# the app is a pass, because both run the same watchdog.ps1. Cleanup
# (clone task, marker, app state) runs in a finally block whether the
# test passes or fails.
#
# Usage (from an elevated PowerShell; ~3-4 min because the task ticks
# every 60 s):
#   powershell -ExecutionPolicy Bypass -File tools\e2e_watchdog_test.ps1
#   powershell -ExecutionPolicy Bypass -File tools\e2e_watchdog_test.ps1 -WaitSeconds 60
#
# Exit code 0 = every case passed, 1 = any failure (use as a release gate).

param(
    [int]$WaitSeconds = 90      # per-case window; the task ticks every 60 s
)

$ErrorActionPreference = "Stop"

# --- preflight ---------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Output "[E2E] FAIL admin: run this script from an elevated PowerShell"
    exit 1
}

$realTask = "SSDTempMonitor Watchdog"
$e2eTask  = "SSDTempMonitor Watchdog E2E"
$procName = "ssd_temp_monitor"
$marker   = Join-Path $env:APPDATA "SSDTempMonitor\watchdog_skip.flag"

$real = Get-ScheduledTask -TaskName $realTask -ErrorAction SilentlyContinue
if (-not $real) {
    Write-Output "[E2E] FAIL preflight: task '$realTask' not installed - install the app first"
    exit 1
}
if ($real.State -eq "Disabled") {
    Write-Output "[E2E] FAIL preflight: task '$realTask' is disabled - enable it first"
    exit 1
}

# locate the app exe the same way watchdog.ps1 does (no hardcoding)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = Split-Path -Parent $here
$candidates = @(
    (Join-Path $appRoot "ssd_temp_monitor.exe"),
    (Join-Path $here "ssd_temp_monitor.exe"),
    "C:\Program Files\SSD Temp Monitor\ssd_temp_monitor.exe"
)
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) {
    Write-Output "[E2E] FAIL preflight: app exe not found"
    exit 1
}
Write-Output "[E2E] app exe: $exe"
Write-Output "[E2E] marker:  $marker"

function Get-AppCount {
    @(Get-Process -Name $procName -ErrorAction SilentlyContinue).Count
}
function Start-AppViaWmi {
    # same spawn method AND flags as watchdog.ps1 (outside any task job
    # object, silent when a duplicate mutex lingers)
    Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{ CommandLine = "`"$exe`" --duplicate-silent" } | Out-Null
}
function Wait-AppCount([int]$min, [int]$timeout) {
    $deadline = (Get-Date).AddSeconds($timeout)
    while ((Get-Date) -lt $deadline) {
        if ((Get-AppCount) -ge $min) { return $true }
        Start-Sleep -Seconds 2
    }
    return ((Get-AppCount) -ge $min)
}
function Wait-AppGone([int]$timeout) {
    # wait until the process table shows ZERO instances - a plain
    # "Wait-AppCount 0" would return instantly (count >= 0 is always true)
    # and the negative window would sample a process that is still dying
    $deadline = (Get-Date).AddSeconds($timeout)
    while ((Get-Date) -lt $deadline) {
        if ((Get-AppCount) -eq 0) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return ((Get-AppCount) -eq 0)
}

$results = @()
function Add-Result([string]$name, [bool]$pass, [double]$seconds) {
    $tag = "FAIL"; if ($pass) { $tag = "PASS" }
    $script:results += @{ name = $name; pass = $pass; seconds = $seconds }
    Write-Output ("[E2E] {0} {1} ({2:n0}s)" -f $tag, $name, $seconds)
}

# --- clone the real task under the test name ----------------------------------
Unregister-ScheduledTask -TaskName $e2eTask -Confirm:$false -ErrorAction SilentlyContinue
$xml = Export-ScheduledTask -TaskName $realTask
Register-ScheduledTask -TaskName $e2eTask -Xml $xml -Force | Out-Null
Write-Output "[E2E] cloned task '$realTask' -> '$e2eTask' (real task stays enabled)"

try {
    # baseline: the app must be running (it is left running afterwards too)
    if ((Get-AppCount) -eq 0) {
        Start-AppViaWmi
        if (-not (Wait-AppCount 1 30)) {
            Write-Output "[E2E] FAIL baseline: app did not start from $exe"
            exit 1
        }
    }
    Write-Output ("[E2E] baseline: app running ({0} procs)" -f (Get-AppCount))

    # --- case 1: crash -> a watchdog task restarts the app --------------------
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Stop-Process -Name $procName -Force -ErrorAction SilentlyContinue
    Wait-AppGone 15 | Out-Null                       # let dying procs leave
    $ok1 = Wait-AppCount 1 $WaitSeconds
    $sw.Stop()
    $parent = "gone"
    $p = Get-CimInstance Win32_Process -Filter "Name='ssd_temp_monitor.exe'" |
         Select-Object -First 1
    if ($p) {
        $pp = Get-Process -Id $p.ParentProcessId -ErrorAction SilentlyContinue
        if ($pp) { $parent = $pp.ProcessName }
    }
    Write-Output ("[E2E] case 1 restarted; parent process: {0}" -f $parent)
    Add-Result "crash-restart" $ok1 $sw.Elapsed.TotalSeconds

    # --- case 2: quit marker -> the watchdog stays silent ---------------------
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    # a fresh machine may not have the data dir yet (the app creates it
    # lazily on first run - a virgin CI runner never got that far)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $marker) | Out-Null
    Set-Content -Path $marker -Value "e2e" -Encoding ASCII
    Stop-Process -Name $procName -Force -ErrorAction SilentlyContinue
    Wait-AppGone 15 | Out-Null
    # negative test: the process count must stay 0 for the whole window;
    # sleep BEFORE the first sample so a process caught mid-death (visible
    # for a moment after TerminateProcess) can never fake a failure
    $ok2 = $true
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if ((Get-Date) -ge $deadline) { break }
        if ((Get-AppCount) -ge 1) { $ok2 = $false; break }
    }
    $sw.Stop()
    Add-Result "marker-suppresses-restart" $ok2 $sw.Elapsed.TotalSeconds
}
finally {
    Remove-Item -Path $marker -Force -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $e2eTask -Confirm:$false -ErrorAction SilentlyContinue
    # leave the machine as we found it: app running
    if ((Get-AppCount) -eq 0) { Start-AppViaWmi; Start-Sleep -Seconds 2 }
}

# --- summary -------------------------------------------------------------------
$passed = @($results | Where-Object { $_.pass }).Count
foreach ($r in $results) {
    $tag = "FAIL"; if ($r.pass) { $tag = "PASS" }
    Write-Output ("[E2E] {0}  {1} ({2:n0}s)" -f $tag, $r.name, $r.seconds)
}
Write-Output ("[E2E] === {0}/{1} passed ===" -f $passed, $results.Count)
# echo the watchdog decision log into the job output - visible directly in
# CI even if the artifact upload step is skipped or the run is local
$wlog = Join-Path $env:APPDATA "SSDTempMonitor\watchdog.log"
if (Test-Path $wlog) {
    Write-Output "--- watchdog.log (tail) ---"
    Get-Content $wlog -Tail 20 | ForEach-Object { Write-Output ("[wd] " + $_) }
}
if ($passed -ne $results.Count) { exit 1 }
exit 0
