# Tests

Unit tests for `ssd_temp_tray.py` — **run without admin, PowerShell, or a GUI**:

```bash
python -m pytest tests/ -v
```

PowerShell is always monkeypatched at the `_run_powershell` boundary; only
pure logic, the CSV history helpers, and the Windows single-instance mutex
are exercised for real.

## Coverage

| Area | What is pinned down |
|---|---|
| `is_internal_ssd` | the original USB-reader bug (ASM236X reports `MediaType=SSD` but must be excluded) |
| `build_update_shim` / `wait_and_install` | update shim waits for the app process (real mutex, real cmd, real wait), then runs the installer and passes its exit code through |
| `select_release_asset(prefer_prerelease)` | stable channel skips `v1.9.0-rc1` tags; pre-release channel sees them |
| `run_unattended_update` | `--update-now`: exit codes and the `cmd /c shim` handoff with SHA-256 verification |
| `PS_TEMPS` / `PS_LIST` | regression guard: the USB filter must run **before** `Get-StorageReliabilityCounter` (the 42-second stall) |
| `read_temps` | client-side filter as defense in depth, even if the query is broken |
| `_safe_temp` | empty string / 0 / 65535 / out-of-range values → `n/a`, never a crash |
| `alert_state` | 65 °C sustained 30 s, 5-min cooldown, reset on drop |
| CSV history | roundtrip, 30-min cutoff, corrupt rows, cap/trim, 60-s flush interval |
| tray icon | cache hit returns the same image object; size/high-contrast settings clamp correctly; high-contrast pill is black with a white border |
| digit fonts (v1.13.0) | 13 families × 4 styles resolve to real `.ttf` files on any machine (family fallback), styles/scale change rendered pixels, scale 50–150 clamped |
| digit colors (v1.13.0) | 8 presets valid, custom `#rrggbb` actually appears on the rendered icon |
| update self-test (v1.13.0) | `run_update_selftest` passes 12/12 and covers version/asset/checksum/shim stages |
| stale `_MEI` cleanup (v1.13.0) | rename-probe: stale dirs removed, fresh/own/locked dirs survive, source runs are a no-op |
| `log_event` | writes structured lines, never raises, rotating handler is configured at import |
| uninstaller | `setup.iss` `[UninstallDelete]` removes `config.json` and the event log |
| single instance | second `acquire_single_instance()` on the same mutex returns `False` |

GUI (tkinter) and tray-icon behavior are intentionally not covered — they
need a desktop session; test those manually via the tray menu.
