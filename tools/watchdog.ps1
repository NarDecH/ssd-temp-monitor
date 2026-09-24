# SSD Temp Monitor - crash watchdog.
# Installed by tools/install_watchdog.ps1 as a 1-minute scheduled task
# (highest privileges). Restarts the installed main app when its process
# is gone - the last line of defense when a native fault kills it.
# Never touches an already-running instance and never starts a second one
# (the app itself has a single-instance mutex as the second net).

$exe = "C:\Program Files\SSD Temp Monitor\ssd_temp_monitor.exe"
$procName = "ssd_temp_monitor"

if (-not (Test-Path $exe)) { exit 0 }          # not installed

$running = Get-Process -Name $procName -ErrorAction SilentlyContinue
if ($running) { exit 0 }                        # healthy - nothing to do

# No process: crash (or update in progress). The updater creates a shim
# cmd.exe with "ssd_temp" in its command line - never restart during that
# window or the silent install gets racing processes.
$updating = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match 'cmd|conhost' -and
        $_.CommandLine -match 'ssd_temp.*setup|ssd_temp.*update'
    }
if ($updating) { exit 0 }

Start-Process -FilePath $exe | Out-Null
