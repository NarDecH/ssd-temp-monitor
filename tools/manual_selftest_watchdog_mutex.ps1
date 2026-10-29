# SSD Temp Monitor - MANUAL self-test for the watchdog + single-instance
# (mutex) paths. Run it yourself from any PowerShell when troubleshooting:
#
#     powershell -ExecutionPolicy Bypass -File tools\manual_selftest_watchdog_mutex.ps1
#     powershell -ExecutionPolicy Bypass -File tools\manual_selftest_watchdog_mutex.ps1 -Help
#     powershell ... -Keep       # keep the scenario data dir for inspection
#
# What it covers, each in an ISOLATED universe (own mutex name, own data
# dir, stub exe) so the production app/task/log are never touched:
#   S1  fresh start          -> app starts, logs the INFO startup event
#   S2  duplicate-silent     -> second start exits 2, no dialog
#   S3  source-run detection -> _source_holder_running() recognizes a live
#                               run-from-source instance from THIS machine
#   S4  foreign holder       -> a foreign mutex holder is logged as
#                               mutex_suspect (samples name the holder)
#                               BEFORE the duplicate exits 2 silently
#   S5  watchdog no-op       -> watchdog run with a live source-run
#                               instance adds NO new watchdog.log line
#   S6  quit-marker skip     -> watchdog_skip.flag -> "skip: quit marker"
#   S7  spawn path           -> nothing running -> "spawned app (pid N)"
#                               against the stub exe (the real task-based
#                               restart window is covered by
#                               tools\e2e_watchdog_test.ps1)
#
# Exit code 0 = all scenarios passed, 1 = any FAIL, 2 = skipped-only.
# Every step prints [MST] PASS/FAIL/SKIP lines and a final verdict.
#
# MUST run from an ELEVATED PowerShell: a fresh app start without admin
# relaunches itself via UAC (relaunch_elevated) and exits 0 immediately,
# so the holding instance never stays alive (S1/S2/S3/S5 collapse).
# Windows PowerShell 5.1 compatible: NO ternary operators, NO ?? operator.

param(
    [switch]$Help,
    [switch]$Keep
)

$ErrorActionPreference = "Continue"

if ($Help) {
    Write-Output "Usage: powershell -ExecutionPolicy Bypass -File tools\manual_selftest_watchdog_mutex.ps1 [-Keep]"
    Write-Output "Runs S1-S7 (watchdog + mutex paths) in an isolated test universe."
    Write-Output "Exit 0 = all passed, 1 = any FAIL, 2 = skipped-only."
    exit 0
}

if (-not $env:OS -or $env:OS -ne "Windows_NT") {
    Write-Output "[MST] FAIL preflight: Windows only"
    exit 1
}

$script:here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:appRoot = Split-Path -Parent $script:here
$script:appSrc  = Join-Path $script:appRoot "ssd_temp_tray.py"
if (-not (Test-Path $script:appSrc)) {
    Write-Output ("[MST] FAIL preflight: ssd_temp_tray.py not found at {0}" -f $script:appRoot)
    exit 1
}
$script:python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $script:python) {
    Write-Output "[MST] FAIL preflight: python not on PATH"
    exit 1
}
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Output "[MST] FAIL preflight: run this script from an ELEVATED PowerShell"
    Write-Output "[MST]       (a non-elevated app start relaunches via UAC and exits 0,"
    Write-Output "[MST]        so no test instance ever stays alive to hold the mutex)"
    exit 1
}

# isolated universe for this run - unique per invocation
$script:stamp    = "{0}{1}" -f $PID, (Get-Date -Format "HHmmss")
$script:dataDir  = Join-Path $env:TEMP ("SSDTempMonitor_selftest_" + $script:stamp)
$script:mutexName = "Local\SSDTempMonitor_SelfTest_" + $script:stamp
$script:wdLog    = Join-Path $script:dataDir "watchdog.log"
$script:appLog   = Join-Path $script:dataDir "ssd_temp_monitor.log"
New-Item -ItemType Directory -Force -Path $script:dataDir | Out-Null

# stub exe for the spawn path: a copy of cmd.exe - the WMI Create only
# needs A working executable; it exits fast with its bogus argument
$script:stubExe = Join-Path $script:dataDir "selftest_stub_cmd.exe"
Copy-Item (Join-Path $env:SystemRoot "System32\cmd.exe") $script:stubExe -Force

$script:results = @()
$script:app1 = $null
$script:app2 = $null
$script:holder = $null

function Add-Result([string]$name, [string]$verdict, [string]$note) {
    $script:results += @{ name = $name; verdict = $verdict; note = $note }
    Write-Output ("[MST] {0,-5} {1}  ({2})" -f $verdict, $name, $note)
}

function Set-AppEnv {
    $env:SSD_TEMP_MUTEX_NAME = $script:mutexName
    $env:SSD_TEMP_DATA_DIR   = $script:dataDir
}
function Clear-AppEnv {
    Remove-Item Env:SSD_TEMP_MUTEX_NAME -ErrorAction SilentlyContinue
    Remove-Item Env:SSD_TEMP_DATA_DIR   -ErrorAction SilentlyContinue
}
function Set-WdEnv {
    Set-AppEnv
    $env:SSD_TEMP_WD_DATA_DIR = $script:dataDir
}
function Clear-WdEnv {
    Clear-AppEnv
    Remove-Item Env:SSD_TEMP_WD_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:SSD_TEMP_WD_EXE      -ErrorAction SilentlyContinue
}
function Start-App {
    Set-AppEnv
    $p = Start-Process -FilePath $script:python `
        -ArgumentList @('"' + $script:appSrc + '"', "--duplicate-silent") `
        -WindowStyle Hidden -PassThru
    return $p
}
function Wait-ProcessGone([System.Diagnostics.Process]$proc, [int]$timeoutSec) {
    if (-not $proc) { return $true }
    if ($proc.HasExited) { return $true }
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        $still = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $still) { return $true }
    }
    return ($null -eq (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue))
}
function Wait-AppLogFor([string]$needle, [int]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ((Test-Path $script:appLog) -and
            ((Select-String -Path $script:appLog -Pattern $needle `
                            -SimpleMatch -ErrorAction SilentlyContinue))) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}
try {
    # ---------------------------------------------------------------- S1
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $script:app1 = Start-App
    $ok = Wait-AppLogFor "INFO startup" 45
    $sw.Stop()
    $tag = "FAIL"; if ($ok) { $tag = "PASS" }
    Add-Result "S1 fresh-start-logs-startup" $tag ("app1 pid " + $script:app1.Id)

    # ---------------------------------------------------------------- S2
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Set-AppEnv
    $dup = Start-Process -FilePath $script:python `
        -ArgumentList @('"' + $script:appSrc + '"', "--duplicate-silent") `
        -WindowStyle Hidden -PassThru -Wait
    Clear-AppEnv
    $sw.Stop()
    $ok = ($dup.ExitCode -eq 2)
    $tag = "FAIL"; if ($ok) { $tag = "PASS" }
    Add-Result "S2 duplicate-silent-exit-2" $tag ("exit=" + $dup.ExitCode)

    # ---------------------------------------------------------------- S3
    # probe run WITHOUT the test-env guard: it must recognize the live
    # python app instance (S1) as a source-run holder from process table
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Clear-AppEnv
    $code = "import sys, os; sys.path.insert(0, r'$script:appRoot'); import ssd_temp_tray as m; os.environ.pop('SSD_TEMP_MUTEX_NAME', None); os.environ.pop('SSD_TEMP_DATA_DIR', None); print('SRC_HOLDER=' + str(m._source_holder_running()))"
    $out = (& $script:python -c $code 2>&1 | Out-String)
    Set-AppEnv
    $sw.Stop()
    $ok = ($out -match "SRC_HOLDER=True")
    $tag = "FAIL"; if ($ok) { $tag = "PASS" }
    $last = ($out.Trim() -split "`r?`n") | Select-Object -Last 1
    Add-Result "S3 source-holder-recognized" $tag $last

    # ---------------------------------------------------------------- S4
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    if ($script:app1 -and -not $script:app1.HasExited) {
        Stop-Process -Id $script:app1.Id -Force -ErrorAction SilentlyContinue
    }
    Wait-ProcessGone $script:app1 15 | Out-Null

    Set-AppEnv
    $readyFile = Join-Path $script:dataDir "holder_ready"
    Remove-Item $readyFile -Force -ErrorAction SilentlyContinue
    $holderCode = "import ctypes,time; ctypes.windll.kernel32.CreateMutexW(None, True, r'$script:mutexName'); open(r'$readyFile', 'w').write('1'); print('holderselftest'); time.sleep(90)"
    $script:holder = Start-Process -FilePath $script:python `
        -ArgumentList @("-c", '"' + $holderCode + '"') `
        -WindowStyle Hidden -PassThru
    Clear-AppEnv
    # wait for the holder to actually own the mutex (marker file written)
    $holderReady = $false
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $readyFile) { $holderReady = $true; break }
        if ($script:holder.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    $dup2 = $null; $suspect = $null; $named = $null
    if ($holderReady) {
        Set-AppEnv
        $dup2 = Start-Process -FilePath $script:python `
            -ArgumentList @('"' + $script:appSrc + '"', "--duplicate-silent") `
            -WindowStyle Hidden -PassThru -Wait
        Clear-AppEnv
        Start-Sleep -Milliseconds 500
        $suspect = Select-String -Path $script:appLog -Pattern "mutex_suspect" `
            -SimpleMatch -ErrorAction SilentlyContinue
        $named = ($suspect | Where-Object { $_.Line -match "holderselftest" })
        Stop-Process -Id $script:holder.Id -Force -ErrorAction SilentlyContinue
    }
    $sw.Stop()
    $ok = ($holderReady -and ($dup2.ExitCode -eq 2) -and $suspect -and $named)
    $tag = "FAIL"; if ($ok) { $tag = "PASS" }
    $note = "holder_ready={0}; exit={1}; suspect_lines={2}; holder_named={3}" -f
        [int][bool]$holderReady, $(if ($dup2) { $dup2.ExitCode } else { "-" }),
        [int][bool]$suspect, [int][bool]$named
    Add-Result "S4 foreign-holder-mutex_suspect" $tag $note

    # ---------------------------------------------------------------- S5
    # watchdog must be a silent no-op while a live source-run instance is
    # detected: run WITHOUT SSD_TEMP_WD_EXE so the src probe stays active
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $script:app2 = Start-App
    if (-not (Wait-AppLogFor "INFO startup" 45)) {
        Add-Result "S5 watchdog-noop-while-live" "FAIL" "app2 did not start"
    } else {
        Set-WdEnv
        $before = 0
        if (Test-Path $script:wdLog) { $before = (Get-Content $script:wdLog).Count }
        & powershell -NoProfile -ExecutionPolicy Bypass `
            -File (Join-Path $script:here "watchdog.ps1") | Out-Null
        $after = 0
        if (Test-Path $script:wdLog) { $after = (Get-Content $script:wdLog).Count }
        Clear-WdEnv
        Set-AppEnv
        $sw.Stop()
        $ok = ($after -eq $before)
        $tag = "FAIL"; if ($ok) { $tag = "PASS" }
        $note = "wd-log lines: {0} -> {1} (must be equal)" -f $before, $after
        Add-Result "S5 watchdog-noop-while-live" $tag $note
    }

    # ---------------------------------------------------------------- S6
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    if ($script:app2 -and -not $script:app2.HasExited) {
        Stop-Process -Id $script:app2.Id -Force -ErrorAction SilentlyContinue
    }
    Wait-ProcessGone $script:app2 15 | Out-Null
    # the app clears the marker on start; S6 needs it present but NO app
    Set-Content -Path (Join-Path $script:dataDir "watchdog_skip.flag") -Value "mst" -Encoding ASCII
    Set-WdEnv
    $env:SSD_TEMP_WD_EXE = $script:stubExe
    $before = 0
    if (Test-Path $script:wdLog) { $before = (Get-Content $script:wdLog).Count }
    & powershell -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $script:here "watchdog.ps1") | Out-Null
    $after = 0
    if (Test-Path $script:wdLog) { $after = (Get-Content $script:wdLog).Count }
    $added = @()
    if ($after -gt $before) { $added = Get-Content $script:wdLog -Tail ($after - $before) }
    Clear-WdEnv
    Set-AppEnv
    $sw.Stop()
    $ok = ($added | Where-Object { $_ -match "skip: quit marker" })
    $tag = "FAIL"; if ($ok) { $tag = "PASS" }
    $note = "added={0} line(s) in isolated wdLog" -f ($added | Measure-Object).Count
    Add-Result "S6 quit-marker-skip" $tag $note

    # ---------------------------------------------------------------- S7
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Remove-Item (Join-Path $script:dataDir "watchdog_skip.flag") -Force -ErrorAction SilentlyContinue
    Set-WdEnv
    $env:SSD_TEMP_WD_EXE = $script:stubExe
    $before = 0
    if (Test-Path $script:wdLog) { $before = (Get-Content $script:wdLog).Count }
    & powershell -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $script:here "watchdog.ps1") | Out-Null
    $after = 0
    if (Test-Path $script:wdLog) { $after = (Get-Content $script:wdLog).Count }
    $added = @()
    if ($after -gt $before) { $added = Get-Content $script:wdLog -Tail ($after - $before) }
    Clear-WdEnv
    Set-AppEnv
    $spawnLine = $added | Where-Object { $_ -match "spawned app \(pid" } | Select-Object -First 1
    $sw.Stop()
    $ok = ($null -ne $spawnLine)
    $tag = "FAIL"; if ($ok) { $tag = "PASS" }
    $note = if ($spawnLine) { ($spawnLine -replace "^.*spawned", "spawned") }
            else { "added: " + (($added | Out-String).Trim()) }
    Add-Result "S7 watchdog-spawns-when-dead" $tag $note
}
finally {
    Clear-WdEnv
    foreach ($p in @($script:app1, $script:app2, $script:holder)) {
        if ($p -and (-not $p.HasExited)) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
    if ($Keep) {
        Write-Output ("[MST] data dir kept for inspection: {0}" -f $script:dataDir)
    } else {
        Remove-Item $script:dataDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- verdict ---------------------------------------------------------------
Write-Output ""
$passed = @($script:results | Where-Object { $_.verdict -eq "PASS" }).Count
$failed = @($script:results | Where-Object { $_.verdict -eq "FAIL" }).Count
foreach ($r in $script:results) {
    Write-Output ("[MST] {0,-5} {1}  ({2})" -f $r.verdict, $r.name, $r.note)
}
Write-Output ("[MST] === verdict: {0}/{1} passed, {2} failed ===" -f $passed, ($script:results | Measure-Object).Count, $failed)
if ($failed -gt 0) {
    Write-Output "[MST] troubleshooting:"
    Write-Output "[MST]  - rerun with -Keep, then inspect the isolated logs:"
    Write-Output "[MST]     %TEMP%\SSDTempMonitor_selftest_*\ssd_temp_monitor.log + watchdog.log"
    Write-Output "[MST]  - S1/S2/S4 are app-side (single-instance, forensics, silent exit)"
    Write-Output "[MST]  - S5/S6/S7 are watchdog-side (probe, quit marker, spawn decision)"
    Write-Output "[MST]  - real-machine evidence stays separate: %APPDATA%\SSDTempMonitor\*.log"
    Write-Output "[MST]  - the task-restart window itself: tools\e2e_watchdog_test.ps1 (elevated)"
    exit 1
}
exit 0
