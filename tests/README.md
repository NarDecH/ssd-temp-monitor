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
| `PS_TEMPS` / `PS_LIST` | regression guard: the USB filter must run **before** `Get-StorageReliabilityCounter` (the 42-second stall) |
| `read_temps` | client-side filter as defense in depth, even if the query is broken |
| `_safe_temp` | empty string / 0 / 65535 / out-of-range values → `n/a`, never a crash |
| `alert_state` | 65 °C sustained 30 s, 5-min cooldown, reset on drop |
| CSV history | roundtrip, 30-min cutoff, corrupt rows, cap/trim, 60-s flush interval |
| tray icon | cache hit returns the same image object |
| single instance | second `acquire_single_instance()` on the same mutex returns `False` |

GUI (tkinter) and tray-icon behavior are intentionally not covered — they
need a desktop session; test those manually via the tray menu.
