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
if (-not $exe) { exit 0 }                        # not installed
$procName = "ssd_temp_monitor"

function Write-WdLog([string]$msg) {
    try {
        $dir = Join-Path $env:APPDATA "SSDTempMonitor"
        if (-not (Test-Path $dir)) {
            $dir = Join-Path (Split-Path -Parent $here) "portable_data"
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
Write-WdLog "app not running - checking guards"

# The app writes this marker when the user quits from the tray menu:
# a deliberate exit is not a crash - do not resurrect. The marker is
# removed again on the next successful app start.
$appRoot = Split-Path -Parent $here
$skipPaths = @(
    (Join-Path $env:APPDATA "SSDTempMonitor\watchdog_skip.flag"),
    (Join-Path $appRoot "portable_data\watchdog_skip.flag")
)
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
