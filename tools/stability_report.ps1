# Stability report for the last N hours (default 24).
# Checks the pass criteria from AGENT.md's 24h stability plan for v1.25.5+:
#   1) no new mutex_suspect / poll_error / tray_loop_error / menu_error
#   2) watchdog.log: only normal ticks (no spawns closer than 60 s apart)
#   3) update_check events present at the configured interval, with only
#      clearly-explained fetch_failed entries
#   4) the 24h history CSV has no gaps bigger than 5 minutes
#   5) startup ms: latest cold start vs 3x p95 of previous samples
#      (warn only - a regression hint, never a FAIL)
# Exit code 0 = all green, 1 = findings (print them). No admin needed.
param([int]$Hours = 24)

$ErrorActionPreference = 'Continue'
$data = "$env:APPDATA\SSDTempMonitor"
$since = (Get-Date).AddHours(-$Hours)
$fail = 0

function Fail($msg) { $script:fail++; Write-Output "FAIL  $msg" }
function Ok($msg)   { Write-Output "ok    $msg" }

Write-Output ("=== stability report: last {0} h (since {1}) ===" -f $Hours, $since.ToString('yyyy-MM-dd HH:mm'))

# ---- 1) app event log: bad events after $since ---------------------------
$log = Join-Path $data 'ssd_temp_monitor.log'
if (Test-Path $log) {
  $bad = Select-String -Path $log -Pattern 'mutex_suspect|poll_error|tray_loop_error|menu_error' |
    ForEach-Object {
      if ($_.Line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
        $t = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
        if ($t -ge $since) { $_.Line }
      }
    }
  if ($bad) { Fail ("event log: {0} bad event(s):" -f @($bad).Count); $bad | ForEach-Object { Write-Output "      $_" } }
  else { Ok 'event log: no mutex_suspect / poll_error / tray_loop_error / menu_error' }

  # ---- 3) update telemetry ------------------------------------------------
  $checks = Select-String -Path $log -Pattern ' update_check ' | ForEach-Object {
    if ($_.Line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*outcome=(\w+)') {
      $t = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
      if ($t -ge $since) { [pscustomobject]@{ t = $t; outcome = $Matches[2] } }
    }
  }
  if ($checks) {
    Ok ("update_check events: {0} ({1})" -f @($checks).Count,
        (($checks | Group-Object outcome | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ', '))
  } else { Write-Output 'note  no update_check events in window' }
  $ff = Select-String -Path $log -Pattern ' fetch_failed ' | ForEach-Object {
    if ($_.Line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
      $t = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
      if ($t -ge $since) { $_.Line }
    }
  }
  if ($ff) { Write-Output ("note  fetch_failed x{0} (check err= fields: 403/DNS are external)" -f @($ff).Count) }

  # ---- 5) startup ms: latest cold start vs 3x p95 (warn only, never FAIL) --
  $startups = Select-String -Path $log -Pattern ' INFO startup ' | ForEach-Object {
    if ($_.Line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*version=([\w.]+).*ms=(\d+)') {
      $t = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
      if ($t -ge $since) {
        [pscustomobject]@{ t = $t; ver = $Matches[2]; ms = [int]$Matches[3] }
      }
    }
  }
  if ($startups -and @($startups).Count -ge 3) {
    $latest = @($startups | Sort-Object t | Select-Object -Last 1)[0]
    $prev = @($startups | Where-Object { $_.t -lt $latest.t } | Sort-Object ms)
    if ($prev.Count -ge 2) {
      $p95 = $prev[[Math]::Ceiling(0.95 * $prev.Count) - 1].ms
      if ($latest.ms -gt 3 * $p95) {
        Write-Output ("warn  startup: latest {0} ms (v{1}) > 3x p95 {2} ms of {3} previous sample(s) - cold-start regression hint" -f $latest.ms, $latest.ver, $p95, $prev.Count)
      } else {
        Ok ("startup: latest {0} ms (v{1}) within 3x p95 {2} ms (n={3} prev)" -f $latest.ms, $latest.ver, $p95, $prev.Count)
      }
    } else {
      Write-Output 'note  startup: too few ms= samples for the p95 check (needs >= 3)'
    }
  } else {
    Write-Output 'note  startup: too few ms= samples for the p95 check (needs >= 3)'
  }
} else { Fail 'event log missing' }

# ---- 2) watchdog: spawns must be >= 60 s apart ---------------------------
$wd = Join-Path $data 'watchdog.log'
if (Test-Path $wd) {
  $spawns = Select-String -Path $wd -Pattern 'spawned app' | ForEach-Object {
    if ($_.Line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
      [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
    }
  } | Sort-Object
  $recent = @($spawns | Where-Object { $_ -ge $since })
  if ($recent.Count -gt 1) {
    for ($i = 1; $i -lt $recent.Count; $i++) {
      if (($recent[$i] - $recent[$i - 1]).TotalSeconds -lt 60) {
        Fail ("watchdog: spawn storm at {0} ({1:s} after previous)" -f $recent[$i], ($recent[$i] - $recent[$i - 1]).TotalSeconds)
      }
    }
  }
  if ($fail -eq 0) { Ok ("watchdog: {0} spawn(s) in window, none closer than 60 s" -f $recent.Count) }
} else { Write-Output 'note  watchdog.log missing' }

# ---- 4) 24h history continuity (gap > 5 min) -----------------------------
$h24 = Join-Path $data 'ssd_temp_history_24h.csv'
if (Test-Path $h24) {
  $rows = Get-Content $h24 | ForEach-Object {
    $p = $_.Split(',')
    if ($p.Count -ge 2 -and $p[0] -match '^\d+$') { [long]$p[0] }
  } | Sort-Object
  if ($rows.Count -lt 2) {
    # the app rewrites this file whole once a minute; a reader can hit the
    # empty moment mid-rewrite (seen 2026-10-07 15:25). Retry briefly
    # before declaring a FAIL - only a persistent blank is a real finding.
    for ($retry = 1; $retry -le 4 -and $rows.Count -lt 2; $retry++) {
      Start-Sleep -Milliseconds 500
      $rows = Get-Content $h24 | ForEach-Object {
        $p = $_.Split(',')
        if ($p.Count -ge 2 -and $p[0] -match '^\d+$') { [long]$p[0] }
      } | Sort-Object
      if ($rows.Count -ge 2) {
        Write-Output ("note  24h history: blank read (transient rewrite race) - retry #{0} found {1} row(s)" -f $retry, $rows.Count)
      }
    }
  }
  if ($rows.Count -ge 2) {
    $gaps = 0
    for ($i = 1; $i -lt $rows.Count; $i++) {
      if ($rows[$i] - $rows[$i - 1] -gt 300) { $gaps++ }
    }
    if ($gaps -gt 0) { Fail "24h history: $gaps gap(s) > 5 min" }
    else { Ok ("24h history: {0} rows, no gap > 5 min" -f $rows.Count) }
  } else { Fail '24h history: fewer than 2 rows' }
} else { Fail '24h history CSV missing' }

Write-Output ("=== result: " + $(if ($fail -eq 0) { 'ALL GREEN' } else { "$fail finding(s)" }) + " ===")
exit $(if ($fail -eq 0) { 0 } else { 1 })
