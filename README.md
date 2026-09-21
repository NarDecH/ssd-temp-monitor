# SSD Temperature Tray Monitor (Windows)

[![CI](https://github.com/NarDecH/ssd-temp-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/NarDecH/ssd-temp-monitor/actions/workflows/ci.yml)
[![Release](https://github.com/NarDecH/ssd-temp-monitor/actions/workflows/release.yml/badge.svg)](https://github.com/NarDecH/ssd-temp-monitor/releases)
![Version](https://img.shields.io/badge/version-1.9.0-orange)
![Tests](https://img.shields.io/badge/tests-142%20passing-brightgreen)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)

![Banner](docs/img/banner.svg)

Shows your SSD temperature live on the system tray icon.

- Icon displays the current temperature (°C) of the hottest SSD, refreshed every 1 second.
- Color coding: **green** ≤ 50 °C, **orange** 51–64 °C, **red** ≥ 65 °C.
- Hover tooltip shows every SSD with its temperature.
- Adjustable **icon size (16–128 px)** and **high-contrast mode** (readable on light taskbars).
- Right-click menu: *Show details*, *Show temperature graph* (last 30 min),
  *Show all disks (debug)*, *Copy diagnostics*, *Settings...*, *Check for updates...*, *About*,
  *Refresh now*, *Record history*, *Exit*.
- **Event log** (rotating) in `%APPDATA%\SSDTempMonitor\ssd_temp_monitor.log`.
- **Internal SSDs only** — USB card readers and enclosures are ignored on
  purpose (their bridge chips report bogus temperatures and can block the
  counter read for ~40 s). See [docs/RESEARCH.md](docs/RESEARCH.md).

## Requirements

- Windows 10/11
- Python 3 with `pip install pystray Pillow`

Temperature is read via `Get-PhysicalDisk | Get-StorageReliabilityCounter`
(filtered to `MediaType = 'SSD'` and `BusType != 'USB'`), which
**requires administrator rights**. The app automatically re-launches
itself with a UAC prompt if it is not already elevated.

## Run

Option A — standalone exe (no Python needed):

    dist\ssd_temp_monitor.exe

Option B — from source (requires `pip install pystray Pillow`):

Double-click `start_ssd_temp_monitor.bat`, or:

    pyw ssd_temp_tray.py

To rebuild the exe: `pip install pyinstaller` then run `build_exe.bat`.

## Auto-update

The app checks GitHub Releases on startup and every 6 hours (configurable,
5–1440 min, takes effect immediately). Two channels are available in
**Settings...**: *stable* or *pre-release* (`v1.9.0-rc1` style tags). When a
new version is published (push a `v*` tag), a notification appears — open
**Check for updates...** to download and install it silently. Every download
is verified against the release's `SHA256SUMS.txt` before installation, and
the installer waits for the app to exit (AppMutex) before upgrading.
Disable with `"check_updates": false` in `%APPDATA%\SSDTempMonitor\config.json`.

For unattended updates (CI / scripting):

    ssd_temp_monitor.exe --update-now

prints `UPDATE-RESULT: ...` lines and exits, so the flow is scriptable.

## History recording (optional)

Enable **Record history** in the tray menu to log the hottest SSD
temperature to `%TEMP%\ssd_temp_history.csv` (last 30 minutes, flushed
every 60 s), then open **Show temperature graph** for a live chart with
min/max/avg. To have it always on, set environment variable
`SSD_TEMP_RECORD_HISTORY=1` before starting.

## Run at startup (optional)

Press Win+R, type `shell:startup`, and put a shortcut to
`start_ssd_temp_monitor.bat` in that folder. Windows will show a UAC
prompt at each login (admin is required for SMART temperature data).

## Documentation

- [docs/README.md](docs/README.md) — Thai user guide (มีฉบับ HTML ด้วย)
- [docs/USER_GUIDE_EN.md](docs/USER_GUIDE_EN.md) — English user guide
- [docs/RESEARCH.md](docs/RESEARCH.md) — why USB readers are excluded
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — release history
- [AGENT.md](AGENT.md) — guidance for AI coding agents
