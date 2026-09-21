# User Guide (English)

![Banner](img/banner.svg)

A tiny single-file Python app that shows your **SSD temperature** live on
the Windows system tray icon (refreshed every second), with warning colors
when a disk runs hot.

> คู่มือภาษาไทย: [README.md](README.md) · HTML: [README.html](README.html)

![Tray icons](img/tray-icons.svg)

## Features

- 🔢 Tray icon shows the temperature (°C) of the **hottest** SSD, refreshed
  every 1 second
- 🎨 Color coding: **green** ≤ 50 °C · **orange** 51–64 °C · **red** ≥ 65 °C

  ![Color scale](img/color-scale.svg)

- 💺 **Per-disk icons** — machines with several NVMe/SATA SSDs get one extra
  tray icon per disk (disable with `"multi_disk_icons": false`)
- 📈 **Live 30-minute graph** with min/max/avg (optional CSV history)
- 🔔 **Overheat alert** — notification when a disk has been ≥ 65 °C for
  30 s (threshold/sustain/cooldown configurable); repeats at most every
  5 minutes
- 🩹 **Copy diagnostics** — one menu click puts version, admin status,
  settings and all disk readings on the clipboard for bug reports
- 🔐 **SHA-256-verified auto-updates** from GitHub Releases
- 🛡️ Automatic UAC elevation (needed to read SMART temperature data)
- 🔌 **Internal SSDs only** — USB card readers / enclosures are ignored on
  purpose: their bridge chips report bogus temperatures and can stall the
  counter read for ~40 s (full analysis in [RESEARCH.md](RESEARCH.md))

## System requirements

- Windows 10 / 11
- Administrator rights (UAC) — SMART temperature data is privileged

## Install

**Option A — installer (recommended):** download
`ssd_temp_monitor_setup_vX.Y.Z.exe` from the
[releases page](https://github.com/NarDecH/ssd-temp-monitor/releases/latest),
run it, and optionally tick *Start automatically at Windows login*.

**Option B — portable exe:** download `ssd_temp_monitor_vX.Y.Z.exe` and run
it directly — no installation.

**Option C — from source:** `pip install pystray Pillow`, then double-click
`start_ssd_temp_monitor.bat` (or `pyw ssd_temp_tray.py`).

## Tray menu

| Menu item | What it does |
|---|---|
| *Show details* | Every disk with model, bus type and temperature |
| *Show temperature graph* | Live chart of the last 30 min, min/max/avg |
| *Show all disks (debug)* | Every physical disk incl. filtered-out ones, with the reason |
| *Copy diagnostics to clipboard* | Snapshot for bug reports |
| *Refresh now* | Read temperatures immediately |
| *Check for updates...* | Download + verify + install a newer release |
| *Settings...* | See below |
| *About* | Version, latest release, releases link |
| *Record history* | Toggle CSV logging for the graph |
| *Exit* | Quit (flushes history first) |

## Settings

Saved to `%APPDATA%\SSDTempMonitor\config.json`; changes apply immediately.

| Setting | Range / values |
|---|---|
| Poll interval | 1–60 s |
| Alert threshold | 40–90 °C |
| Alert sustain | 0–600 s |
| Alert cooldown | 1–120 min |
| History window | 5–240 min |
| Update check interval | 5–1440 min |
| Update channel | `stable` or `pre-release` (sees `v1.9.0-rc1` tags) |
| Icon size | 16–128 px |
| High-contrast icon | black pill + white border (readable on light taskbars) |
| Record history on startup | on/off |
| Multi-disk icons | on/off |

## Auto-update

The app checks GitHub Releases on startup and every N minutes. A newer
stable release raises one notification per release per session; install it
via **Check for updates...**. Every download is SHA-256-verified against
the release's `SHA256SUMS.txt` before it is executed, and the installer
waits for the app to exit before upgrading. Unattended usage:

    ssd_temp_monitor.exe --update-now

## Event log

Startup/shutdown, overheat alerts and update attempts are written to
`%APPDATA%\SSDTempMonitor\ssd_temp_monitor.log` (rotating, 512 kB × 3).

## Run at startup (optional)

The installer can create the startup entry for you. Manual way: press
Win+R, type `shell:startup`, and put a shortcut to the exe there.

## Documentation

- [docs/README.md](README.md) — Thai user guide (HTML: [README.html](README.html))
- [docs/RESEARCH.md](RESEARCH.md) — why USB readers are excluded
- [docs/CHANGELOG.md](CHANGELOG.md) — release history
- [docs/CODE_SIGNING.md](CODE_SIGNING.md) — Authenticode signing status
- [AGENT.md](https://github.com/NarDecH/ssd-temp-monitor/blob/main/AGENT.md) — guidance for AI coding agents
