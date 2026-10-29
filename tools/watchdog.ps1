# SSD Temp Monitor - crash watchdog.
# Copied next to the installed app by the Inno Setup installer
# ({app}\watchdog\watchdog.ps1) or by tools\install_watchdog.ps1, and run
# every minute by the "SSDTempMonitor Watchdog" scheduled task (via the
# invisible wscript VBS launcher).
# Locates the app exe relative to ITSELF: works from the install dir, a
# portable layout, or the repo checkout (tools\..) without hardcoding
# C:\Program Files.
# Restarts the app when its process is gone - the last line of defense when
# a native fault kills it. Never touches an already-running instance and
# never starts a second one (the app itself has a single-instance mutex as
# the second net).
#
# Decision log: the healthy "app is running" no-op is NOT logged (that is
# every minute), so every line in watchdog.log means something - a dead
# app, a guard skip or a spawn result. That is what makes "why did the
# watchdog not restart it?" answerable (and lets tools\e2e_watchdog_test.ps1
# show its evidence).

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates = @(
    (Join-Path (Split-Path -Parent $here) "ssd_temp_monitor.exe"),  # {app}\watchdog\..\
    (Join-Path $here "ssd_temp_monitor.exe"),                        # copy beside the script
    "C:\Program Files\SSD Temp Monitor\ssd_temp_monitor.exe"         # default install dir
)
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) { $exe = $env:SSD_TEMP_WD_EXE }   # self-test hook: ManualTest
                                                 # points the spawn at a stub
if (-not $exe) { exit 0 }                        # not installed
$procName = "ssd_temp_monitor"

function Write-WdLog([string]$msg) {
    try {
        # manual/e2e hook: an isolated data dir keeps self-tests out of the
        # real watchdog.log (same pattern as the app's SSD_TEMP_DATA_DIR)
        $dir = $env:SSD_TEMP_WD_DATA_DIR
        if (-not $dir) { $dir = Join-Path $env:APPDATA "SSDTempMonitor" }
        if (-not (Test-Path $dir)) {
            # fresh machine: the app creates this dir lazily on first run, but
            # the watchdog may want to log BEFORE that ever happened (CI,
            # first boot after install) - a log write that throws silently is
            # exactly the "no evidence" trap this log exists to prevent
            $portable = Join-Path (Split-Path -Parent $here) "portable_data"
            if (Test-Path $portable) {
                $dir = $portable
            } else {
                New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
            }
        }
        $log = Join-Path $dir "watchdog.log"
        if ((Test-Path $log) -and ((Get-Item $log).Length -gt 512KB)) {
            # rotate: keep ONE old part (the app's "Watchdog log" menu item
            # opens the current file or this .old part) instead of silently
            # truncating evidence
            Move-Item -Force $log ($log + ".old")
        }
        Add-Content -Path $log -Encoding UTF8 `
            ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
    } catch { }
}

$running = Get-Process -Name $procName -ErrorAction SilentlyContinue
if ($running) { exit 0 }                        # healthy - nothing to do

# A run-from-repo instance (developer machine, e2e test) shows up as
# python/pythonw with ssd_temp_tray.py on the command line - never as the
# exe process name the probe above knows. Treating it as "dead" made the
# watchdog respawn the exe every minute; every duplicate then hit the
# single-instance mutex and quietly exited, leaving a spawn per minute in
# this log for a full day (seen 2026-10-28, since a pythonw instance
# replaced the exe on this machine). A second live app is a second live
# app - do not resurrect an already running one.
#
# A stub exe from SSD_TEMP_WD_EXE means the caller is probing a controlled
# environment in isolation - the production instance on the same machine
# (different data dir AND mutex) must not collapse the scenario, so the
# src probe is bypassed there.
$srcRunning = $null
if (-not $env:SSD_TEMP_WD_EXE) {
    $srcRunning = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^(python|pythonw)' -and
            $_.CommandLine -match 'ssd_temp_tray\.py'
        }
}
if ($srcRunning) { exit 0 }

Write-WdLog "app not running - checking guards"

# The app writes this marker when the user quits from the tray menu:
# a deliberate exit is not a crash - do not resurrect. The marker is
# removed again on the next successful app start.
#
# self-test isolation: when an explicit data dir is set (manual self-test,
# CI-only use), the marker is looked up ONLY there - production paths must
# never veto a controlled scenario (a real %APPDATA% marker on the same
# machine silently turned the isolated S7 spawn test into a "skip").
$appRoot = Split-Path -Parent $here
if ($env:SSD_TEMP_WD_DATA_DIR) {
    $skipPaths = @((Join-Path $env:SSD_TEMP_WD_DATA_DIR "watchdog_skip.flag"))
} else {
    $skipPaths = @(
        (Join-Path $env:APPDATA "SSDTempMonitor\watchdog_skip.flag"),
        (Join-Path $appRoot "portable_data\watchdog_skip.flag")
    )
}
foreach ($p in $skipPaths) {
    if (Test-Path $p) {
        Write-WdLog "skip: quit marker present ($p)"
        exit 0
    }
}

# No process: crash (or update in progress). The updater creates a shim
# cmd.exe with "ssd_temp" in its command line - never restart during that
# window or the silent install gets racing processes.
$updating = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match 'cmd|conhost' -and
        $_.CommandLine -match 'ssd_temp.*setup|ssd_temp.*update'
    }
if ($updating) {
    Write-WdLog ("skip: update in progress - {0} shim process(es) matched" -f @($updating).Count)
    $updating | ForEach-Object { Write-WdLog ("  matched: {0} :: {1}" -f $_.Name, $_.CommandLine) }
    exit 0
}

# Spawn OUTSIDE this process tree: processes started by a scheduled task
# belong to the task's job object - the task then reports "Running" as long
# as the app lives (later triggers fail with 0x800710E0) and the 5-minute
# ExecutionTimeLimit KILLS the restarted app. Creating the process via WMI
# parents it to WmiPrvSE instead, escaping the task's job entirely.
#
# --duplicate-silent: when a previous instance is mid-death (native crash
# storm) its mutex can outlive it for a few seconds - a duplicate start
# then pops the "already running" MessageBox on the user's desktop, over
# and over with every restart attempt. The flag makes duplicates exit
# quietly (exit code 2); a real fresh start ignores the extra argument.
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
    -Arguments @{ CommandLine = "`"$exe`" --duplicate-silent" }
if ($r.ReturnValue -eq 0) {
    Write-WdLog "spawned app (pid $($r.ProcessId))"
} else {
    # ReturnValue 0 = ok, 2 = access denied, 3 = insufficient privileges,
    # 8 = unknown failure, 9 = path not found, 21 = invalid parameter
    Write-WdLog "WMI spawn FAILED rc=$($r.ReturnValue)"
}
