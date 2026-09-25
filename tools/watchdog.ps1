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

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates = @(
    (Join-Path (Split-Path -Parent $here) "ssd_temp_monitor.exe"),  # {app}\watchdog\..\
    (Join-Path $here "ssd_temp_monitor.exe"),                        # copy beside the script
    "C:\Program Files\SSD Temp Monitor\ssd_temp_monitor.exe"         # default install dir
)
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) { exit 0 }                        # not installed
$procName = "ssd_temp_monitor"

$running = Get-Process -Name $procName -ErrorAction SilentlyContinue
if ($running) { exit 0 }                        # healthy - nothing to do

# The app writes this marker when the user quits from the tray menu:
# a deliberate exit is not a crash - do not resurrect. The marker is
# removed again on the next successful app start.
$appRoot = Split-Path -Parent $here
$skipPaths = @(
    (Join-Path $env:APPDATA "SSDTempMonitor\watchdog_skip.flag"),
    (Join-Path $appRoot "portable_data\watchdog_skip.flag")
)
foreach ($p in $skipPaths) {
    if (Test-Path $p) { exit 0 }
}

# No process: crash (or update in progress). The updater creates a shim
# cmd.exe with "ssd_temp" in its command line - never restart during that
# window or the silent install gets racing processes.
$updating = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match 'cmd|conhost' -and
        $_.CommandLine -match 'ssd_temp.*setup|ssd_temp.*update'
    }
if ($updating) { exit 0 }

# Spawn OUTSIDE this process tree: processes started by a scheduled task
# belong to the task's job object - the task then reports "Running" as long
# as the app lives (later triggers fail with 0x800710E0) and the 5-minute
# ExecutionTimeLimit KILLS the restarted app. Creating the process via WMI
# parents it to WmiPrvSE instead, escaping the task's job entirely.
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = "`"$exe`""
} | Out-Null
