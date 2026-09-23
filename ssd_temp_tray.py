#!/usr/bin/env python3
"""SSD Temperature Tray Monitor.

Shows the temperature of your SSD(s) directly on the Windows system tray
icon, updated every few seconds. Color-coded:
    green  <= 50 C
    orange 51-64 C
    red    >= 65 C

Reads temperature via Get-StorageReliabilityCounter (requires admin), so the
app self-elevates with a UAC prompt if not already running elevated.

Only *internal* SSDs are reported: USB card readers / enclosures often
report MediaType = 'SSD' but expose bogus or missing temperature values
through their bridge chip, so every USB-attached disk is excluded.

Extras:
    - "Show all disks (debug)" lists every disk and why it is included
      or filtered out.
    - Optional CSV history ("Record history" checkbox in the menu) plus a
      "Show temperature graph" window covering the last 30 minutes.
"""

import base64
import csv
import ctypes
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont
import pystray

ICON_SIZE = 64

# ---- auto-update (GitHub Releases) ----
APP_VERSION = "1.21.0"        # keep in sync with setup.iss #define MyAppVersion
UPDATE_CHECK_INTERVAL = 6 * 3600  # fallback only; poll_loop reads SETTINGS

GREEN = "#22c55e"
ORANGE = "#f59e0b"
RED = "#ef4444"
UNKNOWN = "#6b7280"
ACCENT = "#38bdf8"

# temperature sanity range: real SMART readings always fall inside this
TEMP_MIN, TEMP_MAX = -20, 100


# ---- data locations: installed (%APPDATA%) vs portable -------------------
def _is_portable():
    """True for the portable build (a marker file sits beside the exe).

    The installed setup writes to %APPDATA%\\SSDTempMonitor; the portable
    exe keeps config/history next to itself in \\portable_data\\ so it can
    run from a USB stick without leaving traces on the host.
    """
    if not getattr(sys, "frozen", False):
        return False
    base = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.isfile(os.path.join(base, "portable_data.portable"))


BASE_DIR = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False) else os.getcwd())
DATA_DIR = (os.path.join(BASE_DIR, "portable_data") if _is_portable()
            else os.path.join(os.environ.get("APPDATA",
                                             os.path.expanduser("~")),
                              "SSDTempMonitor"))
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
GEOMETRY_FILE = os.path.join(DATA_DIR, "window_geometry.json")
# Digit fonts offered in Settings: {family: {style -> (ttf file, tk name)}}.
# Every family below ships with Windows 10/11; a missing .ttf file or an
# unknown family gracefully falls back to Arial (bold).
FONT_FAMILIES = {
    "Segoe UI": {
        "regular":     ("segoeui.ttf", "Segoe UI"),
        "bold":        ("segoeuib.ttf", "Segoe UI"),
        "italic":      ("segoeuii.ttf", "Segoe UI"),
        "bold italic": ("segoeuiz.ttf", "Segoe UI"),
    },
    "Arial": {
        "regular":     ("arial.ttf", "Arial"),
        "bold":        ("arialbd.ttf", "Arial"),
        "italic":      ("ariali.ttf", "Arial"),
        "bold italic": ("arialbi.ttf", "Arial"),
    },
    "Tahoma": {
        "regular":     ("tahoma.ttf", "Tahoma"),
        "bold":        ("tahomabd.ttf", "Tahoma"),
        "italic":      ("tahoma.ttf", "Tahoma"),          # no true italic
        "bold italic": ("tahomabd.ttf", "Tahoma"),
    },
    "Verdana": {
        "regular":     ("verdana.ttf", "Verdana"),
        "bold":        ("verdanab.ttf", "Verdana"),
        "italic":      ("verdanai.ttf", "Verdana"),
        "bold italic": ("verdanabi.ttf", "Verdana"),
    },
    "Calibri": {
        "regular":     ("calibri.ttf", "Calibri"),
        "bold":        ("calibrib.ttf", "Calibri"),
        "italic":      ("calibrii.ttf", "Calibri"),
        "bold italic": ("calibriz.ttf", "Calibri"),
    },
    "Candara": {
        "regular":     ("Candara.ttf", "Candara"),
        "bold":        ("Candarab.ttf", "Candara"),
        "italic":      ("Candarai.ttf", "Candara"),
        "bold italic": ("Candaraz.ttf", "Candara"),
    },
    "Corbel": {
        "regular":     ("corbel.ttf", "Corbel"),
        "bold":        ("corbelb.ttf", "Corbel"),
        "italic":      ("corbeli.ttf", "Corbel"),
        "bold italic": ("corbelz.ttf", "Corbel"),
    },
    "Franklin Gothic": {
        "regular":     ("framd.ttf", "Franklin Gothic Medium"),
        "bold":        ("fradb.ttf", "Franklin Gothic Medium"),
        "italic":      ("framd.ttf", "Franklin Gothic Medium"),
        "bold italic": ("fradb.ttf", "Franklin Gothic Medium"),
    },
    "Georgia": {
        "regular":     ("georgia.ttf", "Georgia"),
        "bold":        ("georgiab.ttf", "Georgia"),
        "italic":      ("georgiai.ttf", "Georgia"),
        "bold italic": ("georgiaz.ttf", "Georgia"),
    },
    "Trebuchet MS": {
        "regular":     ("trebuc.ttf", "Trebuchet MS"),
        "bold":        ("trebucbd.ttf", "Trebuchet MS"),
        "italic":      ("trebucit.ttf", "Trebuchet MS"),
        "bold italic": ("trebucbi.ttf", "Trebuchet MS"),
    },
    "Consolas": {
        "regular":     ("consola.ttf", "Consolas"),
        "bold":        ("consolab.ttf", "Consolas"),
        "italic":      ("consolai.ttf", "Consolas"),
        "bold italic": ("consolaz.ttf", "Consolas"),
    },
    "Times New Roman": {
        "regular":     ("times.ttf", "Times New Roman"),
        "bold":        ("timesbd.ttf", "Times New Roman"),
        "italic":      ("timesi.ttf", "Times New Roman"),
        "bold italic": ("timesbi.ttf", "Times New Roman"),
    },
    "Courier New": {
        "regular":     ("cour.ttf", "Courier New"),
        "bold":        ("courbd.ttf", "Courier New"),
        "italic":      ("couri.ttf", "Courier New"),
        "bold italic": ("courbi.ttf", "Courier New"),
    },
}

# styles offered in Settings (keys of every FONT_FAMILIES entry)
FONT_STYLES = ("regular", "bold", "italic", "bold italic")

_FONT_FALLBACK = FONT_FAMILIES["Arial"]


def _resolve_font_file(family, style):
    """(ttf file, tk family) for a family/style pair, with fallbacks.

    Unknown family/style -> Arial bold. A missing .ttf file falls back to
    Arial bold too, so the icon can never end up with a broken font.
    """
    if family not in FONT_FAMILIES:
        family, style = "Arial", "bold"
    if style not in FONT_STYLES:
        style = "bold"
    entry = FONT_FAMILIES[family]
    file_and_name = entry.get(style) or entry["bold"]
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isfile(os.path.join(fonts_dir, file_and_name[0])):
        return file_and_name
    # missing file for the requested style: fall back within the family,
    # then to Arial bold (e.g. Franklin Gothic has no real bold italic)
    for alt in ("bold", "regular"):
        cand = entry.get(alt)
        if cand and os.path.isfile(os.path.join(fonts_dir, cand[0])):
            return cand
    return _FONT_FALLBACK["bold"]


# digit color presets offered in Settings; the combobox stays editable so
# any #rrggbb value can be typed or pasted directly
COLOR_PRESETS = ("auto", "#ffffff", "#11111b", "#ffd166",
                 "#ff3b30", "#38bdf8", "#22c55e", "#f472b6")


# icon theme presets: one click applies font+color+contrast combination.
# "custom" = keep whatever the user has set by hand.
THEMES = {
    "custom": {},
    "classic": {"icon_font": "Segoe UI", "icon_font_style": "bold",
                "icon_digit_color": "auto", "high_contrast_icon": False},
    "minimal": {"icon_font": "Segoe UI", "icon_digit_color": "auto",
                "high_contrast_icon": False, "icon_text_dx": 0,
                "icon_text_dy": 0},
    "mono": {"icon_font": "Tahoma", "icon_digit_color": "#ffffff",
             "high_contrast_icon": True},
    "neon": {"icon_font": "Verdana", "icon_digit_color": "#faff00",
             "high_contrast_icon": False},
}


# UI languages offered in the tray submenu and Settings (STRINGS keys)
UI_LANGUAGES = ("en", "th", "ja", "zh")


def _is_hex_color(value):
    """True for "#rgb" or "#rrggbb" strings."""
    s = str(value).strip()
    if not s.startswith("#") or len(s) not in (4, 7):
        return False
    try:
        int(s[1:], 16)
        return True
    except ValueError:
        return False


DEFAULT_SETTINGS = {
    "poll_seconds": 1,
    "alert_threshold": 65,
    "alert_sustain_seconds": 30,
    "alert_cooldown_minutes": 5,
    "history_minutes": 30,                   # 5..1440 (up to 24 h)
    "record_history": True,                  # auto record history
    "smart_alerts": True,                     # proactive SMART notifications
    "multi_disk_icons": True,
    "check_updates": True,
    "github_repo": "NarDech/ssd-temp-monitor",
    "update_channel": "stable",              # or "pre-release"
    "weekly_report_enabled": False,          # auto-save the weekly report
    "weekly_report_dir": "",                 # where; empty = app data dir
    "ui_theme": "auto",                      # auto|dark|light (windows/report)
    "update_check_interval_minutes": 360,     # auto-check every N minutes
    "icon_size": 64,                          # tray icon edge in px
    "high_contrast_icon": False,              # black pill + white border
    "compact_tooltip": True,                  # short "65°C · 58°C (2 disks)"
    "language": "en",                         # UI language: "en" or "th"
    "icon_theme": "classic",                 # theme preset (THEMES keys)
    "icon_font": "Arial",                    # digit font family (FONT_FAMILIES)
    "icon_font_style": "regular",            # regular|bold|italic|bold italic
    "icon_digit_scale": 100,                 # digit size, % of the auto-fit size
    "icon_digit_color": "#ffffff",           # digit color (auto = by contrast)
    "icon_text_dx": 0,                       # digit offset X in px (-50..50)
    "icon_text_dy": 0,                       # digit offset Y in px (-50..50)
}


def _validate_settings(cfg):
    """Keep known keys only, coerce to int/bool and clamp to safe ranges."""
    out = {k: cfg.get(k, d) for k, d in DEFAULT_SETTINGS.items()}
    out["poll_seconds"] = min(60, max(1, int(out["poll_seconds"])))
    out["alert_threshold"] = min(90, max(40, int(out["alert_threshold"])))
    out["alert_sustain_seconds"] = min(600, max(0, int(out["alert_sustain_seconds"])))
    out["alert_cooldown_minutes"] = min(120, max(1, int(out["alert_cooldown_minutes"])))
    out["history_minutes"] = min(1440, max(5, int(out["history_minutes"])))
    out["record_history"] = bool(out["record_history"])
    out["smart_alerts"] = bool(out["smart_alerts"])
    out["update_channel"] = ("pre-release" if out["update_channel"] == "pre-release"
                             else "stable")
    try:
        out["update_check_interval_minutes"] = min(
            1440, max(5, int(out["update_check_interval_minutes"])))
    except (TypeError, ValueError):
        out["update_check_interval_minutes"] = \
            DEFAULT_SETTINGS["update_check_interval_minutes"]
    try:
        out["icon_size"] = min(128, max(16, int(out["icon_size"])))
    except (TypeError, ValueError):
        out["icon_size"] = DEFAULT_SETTINGS["icon_size"]
    out["high_contrast_icon"] = bool(out["high_contrast_icon"])
    out["compact_tooltip"] = bool(out["compact_tooltip"])
    out["weekly_report_enabled"] = bool(out["weekly_report_enabled"])
    out["weekly_report_dir"] = str(out["weekly_report_dir"] or "").strip()
    out["ui_theme"] = (out["ui_theme"] if out["ui_theme"] in
                       ("auto", "dark", "light") else "auto")
    out["language"] = (out["language"] if out["language"] in UI_LANGUAGES
                       else "en")
    out["icon_theme"] = (out["icon_theme"] if out["icon_theme"] in THEMES
                         else "classic")
    if out.get("icon_font") == "auto":      # v1.12.x name for Segoe UI bold
        out["icon_font"] = "Segoe UI"
    out["icon_font"] = (out["icon_font"] if out["icon_font"] in FONT_FAMILIES
                        else "Arial")
    out["icon_font_style"] = (out["icon_font_style"]
                              if out["icon_font_style"] in FONT_STYLES
                              else "regular")
    try:
        out["icon_digit_scale"] = min(150, max(50, int(out["icon_digit_scale"])))
    except (TypeError, ValueError):
        out["icon_digit_scale"] = 100
    dc = str(out["icon_digit_color"]).strip()
    out["icon_digit_color"] = (dc if dc == "auto" or _is_hex_color(dc)
                               else "#ffffff")
    for key in ("icon_text_dx", "icon_text_dy"):
        try:
            out[key] = min(50, max(-50, int(out[key])))
        except (TypeError, ValueError):
            out[key] = 0
    if not str(out["github_repo"]).strip():
        out["github_repo"] = DEFAULT_SETTINGS["github_repo"]
    else:
        out["github_repo"] = str(out["github_repo"]).strip()
    return out


def load_settings(path=None):
    """Read config.json; wrong-typed values are ignored, ranges clamped."""
    path = path or CONFIG_FILE
    cfg = dict(DEFAULT_SETTINGS)
    try:
        # utf-8-sig also reads plain UTF-8 but survives a BOM, which tools
        # like PowerShell 5.1 (Set-Content -Encoding UTF8) prepend; with a
        # plain BOM json.load would fail and every setting would reset.
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        for key, default in DEFAULT_SETTINGS.items():
            if key in data and type(data[key]) is type(default):
                cfg[key] = data[key]
    return _validate_settings(cfg)


def save_settings(settings, path=None):
    """Write settings as JSON; returns True on success."""
    path = path or CONFIG_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError:
        return False


SETTINGS = load_settings()
HISTORY_SECONDS = SETTINGS["history_minutes"] * 60
HISTORY_SAVE_INTERVAL = 60          # flush to disk at most every 60 s
HISTORY_MAX_POINTS = HISTORY_SECONDS  # hard cap (1 point/sec)
# temperature history lives with the other app data (DATA_DIR) so the
# portable build keeps everything in its own folder - the old %TEMP% path
# leaked per-machine state outside the bundle
HISTORY_FILE = os.path.join(DATA_DIR, "ssd_temp_history.csv")
# long-tail history for the 24-hour view: one point per minute, kept
# separately from the fine-grained in-memory history
HISTORY24_FILE = os.path.join(DATA_DIR, "ssd_temp_history_24h.csv")
HISTORY24_CAPACITY = 1440            # 24 h x 60 min, then drop the oldest
HISTORY24_STATE = os.path.join(DATA_DIR, "history24_state.json")
_env_history = os.environ.get("SSD_TEMP_RECORD_HISTORY")
KEEP_HISTORY = (SETTINGS["record_history"] if _env_history is None
                else _env_history == "1")
POLL_SECONDS = SETTINGS["poll_seconds"]

# ---- overheat alert (from settings) ----
ALERT_THRESHOLD = SETTINGS["alert_threshold"]        # °C
ALERT_SUSTAIN_SECONDS = SETTINGS["alert_sustain_seconds"]
ALERT_COOLDOWN_SECONDS = SETTINGS["alert_cooldown_minutes"] * 60

# ---- light/dark UI colors (follows Windows app theme by default) ----
UI_DARK = {"bg": "#0f172a", "panel": "#1e293b", "grid": "#1e293b",
           "axis": "#334155", "text": "#e2e8f0", "muted": "#94a3b8",
           "dim": "#64748b", "entry_bg": "#f8fafc", "head": "#0f172a"}
UI_LIGHT = {"bg": "#f1f5f9", "panel": "#ffffff", "grid": "#e2e8f0",
            "axis": "#94a3b8", "text": "#0f172a", "muted": "#475569",
            "dim": "#64748b", "entry_bg": "#ffffff", "head": "#0f172a"}


def windows_app_is_dark():
    """True when Windows apps are set to the dark theme.

    Reads AppsUseLightTheme from the registry; any failure (other OS,
    missing key, denied access) means "fall back to dark" - the app's
    original look.
    """
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes"
                r"\Personalize") as k:
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return int(val) == 0
    except Exception:
        return True


def ui_palette(mode=None):
    """Color dict for "dark", "light" or "auto" (Windows theme)."""
    mode = mode or SETTINGS.get("ui_theme", "auto")
    if mode == "light":
        return UI_LIGHT
    if mode == "dark":
        return UI_DARK
    return UI_LIGHT if windows_app_is_dark() is False else UI_DARK

# ---- live graph window ----
GRAPH_W, GRAPH_H, GRAPH_PAD = 680, 320, 50

# ---- UI strings (i18n: "en" / "th") -----------------------------------
STRINGS = {
    "en": {
        "app.title": "SSD Temperature Monitor",
        "win.details": "SSD Temperature",
        "win.graph": "SSD Temperature - History",
        "win.disks": "SSD Temperature - All Disks (debug)",
        "win.settings": "SSD Temperature - Settings",
        "tab.health": "Health",
        "health.refresh": "Refresh",
        "health.trend": "30-day trend",
        "health.report": "Weekly report",
        "health.export": "Export...",
        "health.csv": "Export CSV",
        "health.trend_empty": ("No daily health history yet - the app "
                               "records one row per day automatically."),
        "health.status.ok": "All drives look healthy.",
        "health.status.warn": "Attention needed - see details below.",
        "health.no_data": "No disk data available.",
        "health.temp": "Temperature",
        "health.wear": "Wear",
        "health.read_errors": "Read errors",
        "health.unfixed": "uncorrected",
        "settings.smart_alerts": "Alert on SMART problems (new errors, wear)",
        "smart.title": "SSD health notice",
        "smart.new_errors": ("{model}: {n} new uncorrected read error(s) - "
                             "back up your data."),
        "smart.wear_band": "{model}: drive wear reached {wear}%.",
        "smart.wear_slope": ("{model}: wear is rising fast (+{gain}% in "
                             "7 days) - consider a backup plan."),
        "tab.general": "General",
        "tab.icon": "Icon",
        "tab.updates": "Updates",
        "win.about": "About SSD Temperature Monitor",
        "menu.details": "Show details",
        "menu.graph": "Show temperature graph",
        "menu.graph24": "24-hour history",
        "menu.stats": "Usage stats",
        "win.stats": "Usage stats - last 7 days",
        "win.graph24": "Temperature - 24 hours",
        "tab.stats": "Stats",
        "settings.ui_theme": "Window theme (dark/light)",
        "stats.alerts": "Overheat alerts this week: {n}",
        "stats.smart_alerts": "SMART alerts this week: {n}",
        "stats.disk_line": "  min {mn} °C   avg {av} °C   max {mx} °C   wear {wear}",
        "stats.refresh": "Refresh",
        "stats.window": "Based on the daily health log, last 7 days",
        "menu.disks": "Show all disks (debug)",
        "menu.diagnostics": "Copy diagnostics to clipboard",
        "menu.refresh": "Refresh now",
        "menu.updates": "Check for updates...",
        "menu.selftest": "Self-test update system",
        "menu.settings": "Settings...",
        "menu.about": "About",
        "menu.language": "Language",
        "menu.history": "Record history",
        "menu.exit": "Exit",
        "notify.no_update": "No update information available.",
        "notify.latest": "You are running the latest version ({local}).",
        "notify.available": ("Version {remote} is available ({local} installed).\n"
                             "Right-click -> Check for updates... to install."),
        "notify.downloading": "Downloading {version}...",
        "notify.installing": "Installing {version}...",
        "notify.bad_checksum": "Checksum mismatch - update aborted.",
        "notify.download_failed": "Update download failed.",
        "notify.copied": "Diagnostics copied to clipboard.",
        "notify.install_failed": ("The update to {version} could not be installed\n"
                                  "(installer exit code {code}).\n"
                                  "Download it manually from the About window."),
        "notify.title.update": "SSD Temp Monitor — Update",
        "notify.title.available": "SSD Temp Monitor — update available",
        "alert.body": "SSD has been at {peak}°C for a while.",
        "alert.body.no_peak": "SSD is overheating.",
        "alert.title": "⚠ SSD overheat: {peak}°C",
        "alert.title.no_peak": "⚠ SSD overheat",
        "elevation.required": ("Administrator rights are required to read "
                               "SSD temperature."),
        "duplicate.body": ("SSD Temperature Monitor is already running\n"
                           "(check the system tray)."),
        "mb.title": "SSD Temp Monitor",
        "details.no_data": "No SSD temperature data (run as Administrator).",
        "rollback.title": "Update rolled back",
        "rollback.body": ("The update could not start. The previous version "
                          "has been restored and the failed release will be "
                          "skipped."),
        "menu.restore": "Restore previous version...",
        "restore.confirm": ("Put the backed-up previous version back and "
                            "restart the app now?"),
        "restore.none": ("No pre-update backup was found on this machine. "
                         "A backup appears only after an update has been "
                         "installed."),
        "restore.failed": "Could not start the restore process.",
        "graph.no_history": ("No history recorded yet.\n"
                             "Enable 'Record history' in the tray menu."),
        "graph.span": "last {minutes} min",
        "disks.legend": "✓ shown on icon    ✗ filtered out",
        "disks.none": "No disks found.",
        "settings.poll": "Poll interval (seconds, 1-60)",
        "settings.threshold": "Alert threshold (°C, 40-90)",
        "settings.sustain": "Alert sustain (seconds, 0-600)",
        "settings.cooldown": "Alert cooldown (minutes, 1-120)",
        "settings.history": "History window (minutes, 5-1440)",
        "settings.check_interval": "Update check interval (minutes, 5-1440)",
        "settings.font": "Digit font",
        "settings.font_style": "Digit style",
        "settings.digit_scale": "Digit size (% of auto, 50-150)",
        "settings.digit_color": "Digit color (auto or #rrggbb)",
        "settings.dx": "Digit offset X (px, -50..50)",
        "settings.dy": "Digit offset Y (px, -50..50)",
        "settings.preview": "Preview:",
        "settings.theme": "Theme preset",
        "settings.bad_color": ("Digit color must be 'auto' or a hex color\n"
                               "like #ffd166."),
        "settings.record": "Record history on startup",
        "settings.weekly_report": ("Auto-save weekly health report "
                                   "(every 7 days)"),
        "settings.weekly_dir": "Report folder",
        "settings.weekly_browse": "Browse...",
        "settings.channel": "Update channel",
        "settings.icon_size": "Icon size (px, 16-128)",
        "settings.high_contrast": "High-contrast icon (light taskbars)",
        "settings.language": "Language / ภาษา",
        "settings.note": "Values apply immediately - no restart needed.",
        "settings.save": "Save",
        "settings.apply": "Apply",
        "settings.cancel": "Cancel",
        "settings.autostart": "Start automatically at Windows login",
        "settings.compact_tooltip": "Compact tray tooltip (temps only)",
        "graph.export_csv": "Export CSV",
        "graph.export_png": "Export PNG",
        "notify.exported": "Saved: {path}",
        "notify.weekly_saved": ("Weekly health report saved: {path}"),
        "health.wear_high": "SSD wear {wear}% — backup soon",
        "health.wear_used": "SSD wear used: {wear}%",
        "health.read_errors": "{unfixed} UNCORRECTED read errors!",
        "health.read_errors_fixed": "{total} corrected read errors",
        "settings.int_error": "Please enter whole numbers only.",
        "settings.save_error": "Could not write {path}",
        "about.latest.checking": "Latest release: checking…",
        "about.latest.unknown": "Latest release: unknown (offline?)",
        "about.latest.stable_none": "Latest stable: none · newest: {tag} (pre-release)",
        "about.latest.newer": "Latest release: {tag} — update available!",
        "about.latest.uptodate": "Latest release: {tag} — you are up to date",
        "about.check_updates": "Check for updates",
        "about.close": "Close",
        "common.close": "Close",
        "selftest.title": "Update self-test",
        "selftest.pass": (
            "ALL PASSED ({n}/{n} checks)\n\n"
            "version compare · release asset selection · checksum verify\n"
            "update shim (env isolation + detached relaunch)\n\n"
            "The update system is ready for the next release."),
        "selftest.fail": "FAILURES ({n} of {total} checks failed):",
        "diag.header": "SSD Temperature Monitor diagnostics",
        "diag.no_data": "  (no data yet)",
    },
    "th": {
        "app.title": "SSD Temperature Monitor",
        "win.details": "อุณหภูมิ SSD",
        "win.graph": "อุณหภูมิ SSD - ประวัติย้อนหลัง",
        "win.disks": "อุณหภูมิ SSD - ดิสก์ทั้งหมด (debug)",
        "win.settings": "อุณหภูมิ SSD - ตั้งค่า",
        "tab.health": "สุขภาพ",
        "health.refresh": "รีเฟรช",
        "health.trend": "แนวโน้ม 30 วัน",
        "health.report": "รายงานรายสัปดาห์",
        "health.export": "ส่งออก...",
        "health.csv": "ส่งออก CSV",
        "health.trend_empty": "ยังไม่มีประวัติสุขภาพรายวัน - แอปบันทึกวันละหนึ่งแถวโดยอัตโนมัติ",
        "health.status.ok": "ดิสก์ทุกตัวสภาพปกติ",
        "health.status.warn": "ควรตรวจสอบ - ดูรายละเอียดด้านล่าง",
        "health.no_data": "ไม่มีข้อมูลดิสก์",
        "health.temp": "อุณหภูมิ",
        "health.wear": "การสึก",
        "health.read_errors": "ข้อผิดพลาดการอ่าน",
        "health.unfixed": "แก้ไขไม่สำเร็จ",
        "settings.smart_alerts": "แจ้งเตือนเมื่อ SMART มีปัญหา (error ใหม่ / การสึก)",
        "smart.title": "แจ้งเตือนสุขภาพ SSD",
        "smart.new_errors": "{model}: พบข้อผิดพลาดการอ่านแก้ไม่สำเร็จใหม่ {n} รายการ - ควรสำรองข้อมูล",
        "smart.wear_band": "{model}: การสึกถึง {wear}% แล้ว",
        "smart.wear_slope": "{model}: การสึกเพิ่มเร็วผิดปกติ (+{gain}% ใน 7 วัน) - ควรสำรองข้อมูล",
        "tab.general": "ทั่วไป",
        "tab.icon": "ไอคอน",
        "tab.updates": "อัปเดต",
        "win.about": "เกี่ยวกับ SSD Temperature Monitor",
        "menu.details": "ดูรายละเอียด",
        "menu.graph": "แสดงกราฟอุณหภูมิ",
        "menu.graph24": "ประวัติย้อน 24 ชั่วโมง",
        "menu.stats": "สถิติการใช้งาน",
        "win.stats": "สถิติการใช้งาน - 7 วันล่าสุด",
        "win.graph24": "อุณหภูมิ - 24 ชั่วโมง",
        "tab.stats": "สถิติ",
        "settings.ui_theme": "ธีมหน้าต่าง (มืด/สว่าง)",
        "stats.alerts": "แจ้งเตือนอุณหภูมิเกินสัปดาห์นี้: {n} ครั้ง",
        "stats.smart_alerts": "แจ้งเตือน SMART สัปดาห์นี้: {n} ครั้ง",
        "stats.disk_line": "  ต่ำสุด {mn} °C   เฉลี่ย {av} °C   สูงสุด {mx} °C   สึก {wear}",
        "stats.refresh": "รีเฟรช",
        "stats.window": "จากบันทึกสุขภาพรายวัน ย้อนหลัง 7 วัน",
        "menu.disks": "ดูดิสก์ทั้งหมด (debug)",
        "menu.diagnostics": "คัดลอกข้อมูลวินิจฉัย",
        "menu.refresh": "รีเฟรชเดี๋ยวนี้",
        "menu.updates": "ตรวจหาการอัปเดต...",
        "menu.selftest": "ทดสอบระบบอัปเดตด้วยตัวเอง",
        "menu.settings": "ตั้งค่า...",
        "menu.about": "เกี่ยวกับ",
        "menu.language": "ภาษา",
        "menu.history": "บันทึกประวัติ",
        "menu.exit": "ออกจากโปรแกรม",
        "notify.no_update": "ไม่พบข้อมูลการอัปเดต",
        "notify.latest": "คุณใช้เวอร์ชันล่าสุดแล้ว ({local})",
        "notify.available": ("มีเวอร์ชัน {remote} พร้อมใช้ (คุณใช้ {local})\n"
                             "คลิกขวา -> ตรวจหาการอัปเดต... เพื่อติดตั้ง"),
        "notify.downloading": "กำลังดาวน์โหลด {version}...",
        "notify.installing": "กำลังติดตั้ง {version}...",
        "notify.bad_checksum": "Checksum ไม่ตรง - ยกเลิกการอัปเดต",
        "notify.download_failed": "ดาวน์โหลดอัปเดตไม่สำเ็จ",
        "notify.copied": "คัดลอกข้อมูลวินิจฉัยไปคลิปบอร์ดแล้ว",
        "notify.install_failed": ("ติดตั้งอัปเดต {version} ไม่สำเร็จ\n"
                                  "(installer exit code {code})\n"
                                  "ดาวน์โหลดด้วยตัวเองได้จากหน้าเกี่ยวกับ"),
        "notify.title.update": "SSD Temp Monitor — อัปเดต",
        "notify.title.available": "SSD Temp Monitor — มีเวอร์ชันใหม่",
        "alert.body": "SSD มีอุณหภูมิ {peak}°C ติดกันหลายวินาที",
        "alert.title": "⚠ SSD ร้อนเกิน: {peak}°C",
        "alert.body.no_peak": "SSD กำลังร้อนเกินไป",
        "alert.title.no_peak": "⚠ SSD ร้อนเกินไป",
        "elevation.required": "ต้องใช้สิทธิ์ Administrator เพื่ออ่านอุณหภูมิ SSD",
        "duplicate.body": ("SSD Temperature Monitor กำลังทำงานอยู่แล้ว\n"
                           "(ดูที่ system tray)"),
        "mb.title": "SSD Temp Monitor",
        "details.no_data": "ไม่พบข้อมูลอุณหภูมิ (ต้องรันในสิทธิ์ Administrator)",
        "rollback.title": "ย้อนกลับการอัปเดตแล้ว",
        "rollback.body": ("การอัปเดตไม่สามารถเริ่มทำงานได้ ระบบจึงกลับไปใช้เวอร์ชันเดิม"
                          "และจะข้ามเวอร์ชันที่มีปัญหานี้ในการตรวจสอบครั้งถัดไป"),
        "menu.restore": "คืนเวอร์ชันก่อนหน้า...",
        "restore.confirm": ("ต้องการคืนเวอร์ชันสำรองก่อนหน้าและเริ่มโปรแกรมใหม่"
                            "ทันทีหรือไม่?"),
        "restore.none": ("ไม่พบไฟล์สำรองก่อนอัปเดตบนเครื่องนี้ ไฟล์สำรองจะเกิดขึ้น"
                         "ก็ต่อเมื่อมีการติดตั้งอัปเดตแล้วเท่านั้น"),
        "restore.failed": "เริ่มกระบวนการคืนเวอร์ชันไม่สำเร็จ",
        "graph.no_history": ("ยังไม่มีประวัติบันทึก\n"
                             "เปิด 'บันทึกประวัติ' จากเมนูที่ไอคอน tray"),
        "graph.span": "{minutes} นาทีล่าสุด",
        "disks.legend": "✓ แสดงบนไอคอน    ✗ ถูกกรองออก",
        "disks.none": "ไม่พบดิสก์",
        "settings.poll": "ช่วงอ่านอุณหภูมิ (วินาที, 1-60)",
        "settings.threshold": "อุณหภูมิแจ้งเตือน (°C, 40-90)",
        "settings.sustain": "เวลาที่ต้องร้อนติดกัน (วินาที, 0-600)",
        "settings.cooldown": "ช่วงเว้นการแจ้งซ้ำ (นาที, 1-120)",
        "settings.history": "ความยาวประวัติ (นาที, 5-1440)",
        "settings.check_interval": "ช่วงเวลาตรวจอัปเดต (นาที, 5-1440)",
        "settings.font": "ฟอนต์ตัวเลข",
        "settings.font_style": "หนา-เอียงตัวเลข",
        "settings.digit_scale": "ขนาดตัวเลข (% ของอัตโนมัติ, 50-150)",
        "settings.digit_color": "สีตัวเลข (auto หรือ #rrggbb)",
        "settings.dx": "เลื่อนตัวเลขแกน X (px, -50..50)",
        "settings.dy": "เลื่อนตัวเลขแกน Y (px, -50..50)",
        "settings.preview": "ตัวอย่าง:",
        "settings.theme": "ธีมสำเร็จรูป",
        "settings.bad_color": ("สีตัวเลขต้องเป็น 'auto' หรือโค้ดสี\n"
                               "แบบ #ffd166"),
        "settings.record": "บันทึกประวัติตอนเปิดโปรแกรม",
        "settings.weekly_report": ("บันทึกรายงานสุขภาพรายสัปดาห์อัตโนมัติ "
                                   "(ทุก 7 วัน)"),
        "settings.weekly_dir": "โฟลเดอร์เก็บรายงาน",
        "settings.weekly_browse": "เลือก...",
        "settings.channel": "ช่องทางอัปเดต",
        "settings.icon_size": "ขนาดไอคอน (px, 16-128)",
        "settings.high_contrast": "ไอคอนคมชัดพิเศษ (taskbar สีอ่อน)",
        "settings.language": "ภาษา / Language",
        "settings.note": "ค่าทั้งหมดมีผลทันที - ไม่ต้องรีสตาร์ท",
        "settings.save": "บันทึก",
        "settings.apply": "ใช้ค่า",
        "settings.cancel": "ยกเลิก",
        "settings.autostart": "เริ่มโปรแกรมอัตโนมัติตอนเข้าสู่ระบบ Windows",
        "settings.compact_tooltip": "tooltip แบบย่อ (แสดงแค่อุณหภูมิ)",
        "graph.export_csv": "บันทึก CSV",
        "graph.export_png": "บันทึก PNG",
        "notify.exported": "บันทึกแล้ว: {path}",
        "notify.weekly_saved": ("บันทึกรายงานสุขภาพรายสัปดาห์แล้ว: {path}"),
        "health.wear_high": "SSD สึกแล้ว {wear}% — ควรสำรองข้อมูลเร็ว ๆ นี้",
        "health.wear_used": "SSD ใช้ไปแล้ว: {wear}%",
        "health.read_errors": "Read error แก้ไม่ได้ {unfixed} ครั้ง!",
        "health.read_errors_fixed": "Read error แก้ไขแล้ว {total} ครั้ง",
        "settings.int_error": "กรุณากรอกตัวเลขจำนวนเต็มเท่านั้น",
        "settings.save_error": "เขียนไฟล์ {path} ไม่สำเร็จ",
        "about.latest.checking": "เวอร์ชันล่าสุด: กำลังตรวจ…",
        "about.latest.unknown": "เวอร์ชันล่าสุด: ไม่ทราบ (ออฟไลน์?)",
        "about.latest.stable_none": "stable ล่าสุด: ไม่มี · ใหม่สุด: {tag} (pre-release)",
        "about.latest.newer": "เวอร์ชันล่าสุด: {tag} — มีเวอร์ชันใหม่!",
        "about.latest.uptodate": "เวอร์ชันล่าสุด: {tag} — คุณใช้ล่าสุดแล้ว",
        "about.check_updates": "ตรวจหาการอัปเดต",
        "about.close": "ปิด",
        "common.close": "ปิด",
        "selftest.title": "ทดสอบระบบอัปเดต",
        "selftest.pass": (
            "ผ่านทั้งหมด ({n}/{n} รายการ)\n\n"
            "เปรียบเทียบเวอร์ชัน · เลือกไฟล์ release · ตรวจ checksum\n"
            "update shim (กัน env ค้าง + relaunch แบบ detached)\n\n"
            "ระบบอัปเดตพร้อมสำหรับ release ถัดไป"),
        "selftest.fail": "ไม่ผ่าน ({n} จาก {total} รายการ):",
        "diag.header": "SSD Temperature Monitor diagnostics",
        "diag.no_data": "  (ยังไม่มีข้อมูล)",
    },
    "ja": {
        "app.title": "SSD Temperature Monitor",
        "win.details": "SSD 温度",
        "win.graph": "SSD 温度 - 履歴",
        "win.disks": "SSD 温度 - 全ディスク (デバッグ)",
        "win.settings": "SSD 温度 - 設定",
        "tab.health": "健全性",
        "health.refresh": "更新",
        "health.trend": "30日トレンド",
        "health.report": "週次レポート",
        "health.export": "エクスポート...",
        "health.csv": "CSV書き出し",
        "health.trend_empty": "日次の健康履歴はまだありません - アプリが毎日自動で1行記録します。",
        "health.status.ok": "すべてのドライブは正常です。",
        "health.status.warn": "要確認 - 下記の詳細をご覧ください。",
        "health.no_data": "ディスク情報がありません。",
        "health.temp": "温度",
        "health.wear": "劣化度",
        "health.read_errors": "読み取りエラー",
        "health.unfixed": "訂正不能",
        "settings.smart_alerts": "SMART 異常を通知 (新規エラー / 劣化)",
        "smart.title": "SSD 健康のお知らせ",
        "smart.new_errors": "{model}: 新しい訂正不能読み取りエラーが {n} 件 - データをバックアップしてください。",
        "smart.wear_band": "{model}: ドライブの劣化が {wear}% に到達しました。",
        "smart.wear_slope": "{model}: 劣化が急上昇しています (7日で +{gain}%) - バックアップをご検討ください。",
        "tab.general": "全般",
        "tab.icon": "アイコン",
        "tab.updates": "更新",
        "win.about": "SSD Temperature Monitor について",
        "menu.details": "詳細を表示",
        "menu.graph": "温度グラフを表示",
        "menu.graph24": "24時間履歴",
        "menu.stats": "使用統計",
        "win.stats": "使用統計 - 過去7日間",
        "win.graph24": "温度 - 24時間",
        "tab.stats": "統計",
        "settings.ui_theme": "ウィンドウテーマ（ダーク/ライト）",
        "stats.alerts": "今週の過熱警告: {n}回",
        "stats.smart_alerts": "今週のSMART警告: {n}回",
        "stats.disk_line": "  最小 {mn} °C   平均 {av} °C   最大 {mx} °C   摩耗 {wear}",
        "stats.refresh": "更新",
        "stats.window": "日次ヘルスログより過去7日間",
        "menu.disks": "全ディスクを表示 (デバッグ)",
        "menu.diagnostics": "診断情報をクリップボードへコピー",
        "menu.refresh": "今すぐ更新",
        "menu.updates": "アップデートを確認...",
        "menu.selftest": "更新システムの自己テスト",
        "menu.settings": "設定...",
        "menu.about": "このアプリについて",
        "menu.language": "言語",
        "menu.history": "履歴を記録",
        "menu.exit": "終了",
        "notify.no_update": "アップデート情報がありません。",
        "notify.latest": "最新バージョン ({local}) を使用中です。",
        "notify.available": ("バージョン {remote} が利用可能です (現在 {local})。\n"
                             "右クリック → アップデートを確認... でインストールできます。"),
        "notify.downloading": "{version} をダウンロード中...",
        "notify.installing": "{version} をインストール中...",
        "notify.bad_checksum": "チェックサム不一致 - アップデートを中止しました。",
        "notify.download_failed": "アップデートのダウンロードに失敗しました。",
        "notify.copied": "診断情報をクリップボードにコピーしました。",
        "notify.install_failed": ("{version} へのアップデートをインストールできませんでした\n"
                                  "(インストーラー終了コード {code})。\n"
                                  "About ウィンドウから手動でダウンロードしてください。"),
        "notify.title.update": "SSD Temp Monitor — 更新",
        "notify.title.available": "SSD Temp Monitor — 更新あり",
        "alert.body": "SSD が {peak}°C をしばらく超えています。",
        "alert.body.no_peak": "SSD が過熱しています。",
        "alert.title": "⚠ SSD 過熱: {peak}°C",
        "alert.title.no_peak": "⚠ SSD 過熱",
        "elevation.required": "SSD 温度の取得には管理者権限が必要です。",
        "duplicate.body": ("SSD Temperature Monitor は既に起動しています\n"
                           "(タスクトレイを確認してください)。"),
        "mb.title": "SSD Temp Monitor",
        "details.no_data": "SSD 温度データがありません (管理者として実行)。",
        "rollback.title": "アップデートをロールバックしました",
        "rollback.body": ("アップデートを開始できませんでした。以前のバージョンを復元し、"
                          "問題のあるリリースは今後スキップされます。"),
        "menu.restore": "前のバージョンに戻す...",
        "restore.confirm": ("バックアップされた以前のバージョンを戻して、"
                            "今すぐアプリを再起動しますか？"),
        "restore.none": ("このマシンにアップデート前のバックアップが見つかりません。"
                         "バックアップはアップデート適用後に作成されます。"),
        "restore.failed": "復元プロセスを開始できませんでした。",
        "graph.no_history": ("履歴がまだありません。\n"
                             "トレイメニューで「履歴を記録」を有効にしてください。"),
        "graph.span": "過去 {minutes} 分",
        "disks.legend": "✓ アイコン表示    ✗ 除外",
        "disks.none": "ディスクが見つかりません。",
        "settings.poll": "取得間隔 (秒, 1-60)",
        "settings.threshold": "警報しきい値 (°C, 40-90)",
        "settings.sustain": "警報持続時間 (秒, 0-600)",
        "settings.cooldown": "再警報までの間隔 (分, 1-120)",
        "settings.history": "履歴の長さ (分, 5-1440)",
        "settings.check_interval": "更新確認間隔 (分, 5-1440)",
        "settings.font": "数字フォント",
        "settings.font_style": "数字スタイル",
        "settings.digit_scale": "数字サイズ (自動の %, 50-150)",
        "settings.digit_color": "数字の色 (auto または #rrggbb)",
        "settings.dx": "数字オフセット X (px, -50..50)",
        "settings.dy": "数字オフセット Y (px, -50..50)",
        "settings.preview": "プレビュー:",
        "settings.theme": "テーマ",
        "settings.bad_color": ("数字の色は 'auto' または 16 進カラー\n"
                               "(例: #ffd166) で指定してください。"),
        "settings.record": "起動時に履歴を記録",
        "settings.weekly_report": ("週次ヘルスレポートを自動保存 "
                                   "（7日ごと）"),
        "settings.weekly_dir": "レポート保存先",
        "settings.weekly_browse": "参照...",
        "settings.channel": "更新チャンネル",
        "settings.icon_size": "アイコンサイズ (px, 16-128)",
        "settings.high_contrast": "ハイコントラストアイコン (明るいタスクバー用)",
        "settings.language": "言語 / Language",
        "settings.note": "設定は即時反映されます - 再起動不要。",
        "settings.save": "保存",
        "settings.apply": "適用",
        "settings.cancel": "キャンセル",
        "settings.autostart": "Windows サインイン時に自動起動",
        "settings.compact_tooltip": "コンパクトなトレイのヒント (温度のみ)",
        "graph.export_csv": "CSV を保存",
        "graph.export_png": "PNG を保存",
        "notify.exported": "保存しました: {path}",
        "notify.weekly_saved": "週次レポートを保存しました: {path}",
        "health.wear_high": "SSD 劣化 {wear}% — 早めのバックアップを",
        "health.wear_used": "SSD 劣化使用率: {wear}%",
        "health.read_errors": "修復不能な読み取りエラー {unfixed} 件!",
        "health.read_errors_fixed": "修復済み読み取りエラー {total} 件",
        "settings.int_error": "整数を入力してください。",
        "settings.save_error": "{path} に書き込めませんでした",
        "about.latest.checking": "最新リリース: 確認中…",
        "about.latest.unknown": "最新リリース: 不明 (オフライン?)",
        "about.latest.stable_none": "stable 最新: なし · 新しい: {tag} (pre-release)",
        "about.latest.newer": "最新リリース: {tag} — 更新があります!",
        "about.latest.uptodate": "最新リリース: {tag} — 最新です",
        "about.check_updates": "アップデートを確認",
        "about.close": "閉じる",
        "common.close": "閉じる",
        "selftest.title": "更新システムの自己テスト",
        "selftest.pass": ("すべて合格 ({n}/{n} 項目)\n\n"
                          "バージョン比較 · リリース資産選択 · チェックサム検証\n"
                          "更新 shim (環境変数分離 + 分離再起動)\n\n"
                          "更新システムは次のリリースに向け準備完了です。"),
        "selftest.fail": "失敗 ({total} 項目中 {n} 件):",
        "diag.header": "SSD Temperature Monitor 診断",
        "diag.no_data": "  (データなし)",
    },
    "zh": {
        "app.title": "SSD 温度监控",
        "win.details": "SSD 温度",
        "win.graph": "SSD 温度 - 历史记录",
        "win.disks": "SSD 温度 - 全部磁盘 (调试)",
        "win.settings": "SSD 温度 - 设置",
        "tab.health": "健康",
        "health.refresh": "刷新",
        "health.trend": "30 天趋势",
        "health.report": "每周报告",
        "health.export": "导出...",
        "health.csv": "导出 CSV",
        "health.trend_empty": "尚无每日健康历史 - 应用会每天自动记录一行。",
        "health.status.ok": "所有磁盘状态正常。",
        "health.status.warn": "需要关注 - 请查看下方详情。",
        "health.no_data": "没有磁盘数据。",
        "health.temp": "温度",
        "health.wear": "磨损",
        "health.read_errors": "读取错误",
        "health.unfixed": "无法纠正",
        "settings.smart_alerts": "SMART 异常提醒 (新错误 / 磨损)",
        "smart.title": "SSD 健康提醒",
        "smart.new_errors": "{model}: 新增 {n} 个无法纠正的读取错误 - 请备份数据。",
        "smart.wear_band": "{model}: 磨损已达 {wear}%。",
        "smart.wear_slope": "{model}: 磨损上升过快 (7 天 +{gain}%) - 建议备份数据。",
        "tab.general": "常规",
        "tab.icon": "图标",
        "tab.updates": "更新",
        "win.about": "关于 SSD Temperature Monitor",
        "menu.details": "显示详情",
        "menu.graph": "显示温度曲线",
        "menu.graph24": "24 小时历史",
        "menu.stats": "使用统计",
        "win.stats": "使用统计 - 过去 7 天",
        "win.graph24": "温度 - 24 小时",
        "tab.stats": "统计",
        "settings.ui_theme": "窗口主题（深色/浅色）",
        "stats.alerts": "本周过热警报: {n} 次",
        "stats.smart_alerts": "本周 SMART 警报: {n} 次",
        "stats.disk_line": "  最低 {mn} °C   平均 {av} °C   最高 {mx} °C   磨损 {wear}",
        "stats.refresh": "刷新",
        "stats.window": "基于每日健康日志，过去 7 天",
        "menu.disks": "显示全部磁盘 (调试)",
        "menu.diagnostics": "复制诊断信息到剪贴板",
        "menu.refresh": "立即刷新",
        "menu.updates": "检查更新...",
        "menu.selftest": "自检更新系统",
        "menu.settings": "设置...",
        "menu.about": "关于",
        "menu.language": "语言",
        "menu.history": "记录历史",
        "menu.exit": "退出",
        "notify.no_update": "没有可用的更新信息。",
        "notify.latest": "您正在运行最新版本 ({local})。",
        "notify.available": ("新版本 {remote} 可用 (当前 {local})。\n"
                             "右键点击 → 检查更新... 即可安装。"),
        "notify.downloading": "正在下载 {version}...",
        "notify.installing": "正在安装 {version}...",
        "notify.bad_checksum": "校验和不匹配 - 已中止更新。",
        "notify.download_failed": "更新下载失败。",
        "notify.copied": "诊断信息已复制到剪贴板。",
        "notify.install_failed": ("无法安装 {version} 更新\n"
                                  "(安装程序退出代码 {code})。\n"
                                  "请从“关于”窗口手动下载。"),
        "notify.title.update": "SSD Temp Monitor — 更新",
        "notify.title.available": "SSD Temp Monitor — 有可用更新",
        "alert.body": "SSD 已持续处于 {peak}°C 一段时间。",
        "alert.body.no_peak": "SSD 正在过热。",
        "alert.title": "⚠ SSD 过热: {peak}°C",
        "alert.title.no_peak": "⚠ SSD 过热",
        "elevation.required": "读取 SSD 温度需要管理员权限。",
        "duplicate.body": ("SSD Temperature Monitor 已在运行\n"
                           "(请查看系统托盘)。"),
        "mb.title": "SSD Temp Monitor",
        "details.no_data": "没有 SSD 温度数据 (请以管理员身份运行)。",
        "rollback.title": "更新已回滚",
        "rollback.body": ("无法启动更新。已恢复之前的版本，后续检查将跳过"
                          "这个有问题的版本。"),
        "menu.restore": "恢复上一个版本...",
        "restore.confirm": ("恢复备份的上一版本并立即重新启动应用吗？"),
        "restore.none": ("在这台电脑上找不到更新前的备份。备份只会在安装"
                         "更新之后才会出现。"),
        "restore.failed": "无法启动恢复过程。",
        "graph.no_history": ("尚无历史记录。\n"
                             "请在托盘菜单中启用“记录历史”。"),
        "graph.span": "最近 {minutes} 分钟",
        "disks.legend": "✓ 显示在图标    ✗ 已过滤",
        "disks.none": "未找到磁盘。",
        "settings.poll": "采集间隔 (秒, 1-60)",
        "settings.threshold": "报警阈值 (°C, 40-90)",
        "settings.sustain": "报警持续时间 (秒, 0-600)",
        "settings.cooldown": "重复报警间隔 (分钟, 1-120)",
        "settings.history": "历史时长 (分钟, 5-1440)",
        "settings.check_interval": "检查更新间隔 (分钟, 5-1440)",
        "settings.font": "数字字体",
        "settings.font_style": "数字样式",
        "settings.digit_scale": "数字大小 (自动的 %, 50-150)",
        "settings.digit_color": "数字颜色 (auto 或 #rrggbb)",
        "settings.dx": "数字偏移 X (px, -50..50)",
        "settings.dy": "数字偏移 Y (px, -50..50)",
        "settings.preview": "预览:",
        "settings.theme": "主题预设",
        "settings.bad_color": ("数字颜色必须是 'auto' 或十六进制颜色\n"
                               "(如 #ffd166)。"),
        "settings.record": "启动时记录历史",
        "settings.weekly_report": ("自动保存每周健康报告 "
                                   "（每 7 天）"),
        "settings.weekly_dir": "报告文件夹",
        "settings.weekly_browse": "浏览...",
        "settings.channel": "更新通道",
        "settings.icon_size": "图标大小 (px, 16-128)",
        "settings.high_contrast": "高对比度图标 (浅色任务栏)",
        "settings.language": "语言 / Language",
        "settings.note": "所有设置立即生效 - 无需重启。",
        "settings.save": "保存",
        "settings.apply": "应用",
        "settings.cancel": "取消",
        "settings.autostart": "登录 Windows 时自动启动",
        "settings.compact_tooltip": "紧凑托盘提示 (仅温度)",
        "graph.export_csv": "导出 CSV",
        "graph.export_png": "导出 PNG",
        "notify.exported": "已保存: {path}",
        "notify.weekly_saved": "每周报告已保存: {path}",
        "health.wear_high": "SSD 磨损 {wear}% — 请尽快备份",
        "health.wear_used": "SSD 磨损: {wear}%",
        "health.read_errors": "{unfixed} 个无法修复的读取错误!",
        "health.read_errors_fixed": "{total} 个已修复的读取错误",
        "settings.int_error": "请只输入整数。",
        "settings.save_error": "无法写入 {path}",
        "about.latest.checking": "最新版本: 检查中…",
        "about.latest.unknown": "最新版本: 未知 (离线?)",
        "about.latest.stable_none": "stable 最新: 无 · 最新: {tag} (pre-release)",
        "about.latest.newer": "最新版本: {tag} — 有可用更新!",
        "about.latest.uptodate": "最新版本: {tag} — 已是最新",
        "about.check_updates": "检查更新",
        "about.close": "关闭",
        "common.close": "关闭",
        "selftest.title": "更新系统自检",
        "selftest.pass": ("全部通过 ({n}/{n} 项)\n\n"
                          "版本比较 · release 资产选择 · 校验和验证\n"
                          "更新 shim (环境隔离 + 分离重启)\n\n"
                          "更新系统已为下一个版本准备就绪。"),
        "selftest.fail": "失败 ({total} 项中的 {n} 项):",
        "diag.header": "SSD Temperature Monitor 诊断",
        "diag.no_data": "  (暂无数据)",
    },
}


def tr(key, **kw):
    """Translate a UI string; {placeholders} come from keyword arguments.

    Falls back to English, then to the key itself - never raises.
    """
    lang = str(SETTINGS.get("language", "en"))
    text = STRINGS.get(lang, STRINGS["en"]).get(key) \
        or STRINGS["en"].get(key) or key
    try:
        return text.format(**kw) if kw else text
    except (KeyError, IndexError):
        return text


# ---- rotating event log (startup/shutdown/update/alert/error) -------------
LOG_FILE = os.path.join(DATA_DIR, "ssd_temp_monitor.log")
_event_log = logging.getLogger("ssd_temp_monitor")
_event_log.setLevel(logging.INFO)
try:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    _handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"))
    _event_log.addHandler(_handler)
except OSError:
    _event_log.addHandler(logging.NullHandler())

# "2026-09-23 14:12:03,841" prefix of every log line (for week_stats)
LOG_LINE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


def log_event(event, **fields):
    """Append one structured line to the rotating event log.

    Never raises - logging must not be able to break the app.
    """
    try:
        parts = " ".join(
            f"{k}={v}" for k, v in fields.items() if v is not None)
        _event_log.info("%s%s", event, (" " + parts) if parts else "")
    except Exception:
        pass
GRAPH_Y_LO, GRAPH_Y_HI = 15, 95
GRAPH_REFRESH_MS = 1000

# ---- single instance ----
MUTEX_NAME = "Local\\SSDTempMonitor_SingleInstance"

_ICON_CACHE = {}


# use_last_error=True is REQUIRED: plain windll does not preserve the
# thread's LastError, so GetLastError() would be unreliable here
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def acquire_single_instance() -> bool:
    """Create a named mutex; return False if another instance is running."""
    try:
        _kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated() -> None:
    """Relaunch this script (or the frozen exe) with a UAC prompt."""
    if getattr(sys, "frozen", False):
        # PyInstaller build: elevate the exe itself (forward CLI flags,
        # e.g. --update-now, across the UAC boundary)
        target = sys.executable
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        target = sys.executable
        script = os.path.abspath(__file__)
        params = f'"{script}" ' + " ".join(f'"{a}"' for a in sys.argv[1:])
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", target, params.strip(), None, 1
    )
    if ret <= 32:  # user declined UAC
        ctypes.windll.user32.MessageBoxW(
            None, tr("elevation.required"), tr("mb.title"),
            0x10 | 0x40000 | 0x10000  # error icon | topmost | set foreground
        )
    sys.exit(0)


def read_file_version(path):
    """Read the embedded VERSIONINFO (File Version) of an exe.

    Returns the string FileVersion (e.g. "1.14.0-rc2", "10.0.26100.1 ...")
    when available - it preserves semver pre-release suffixes that the
    numeric FILEVERSION resource cannot carry - otherwise falls back to
    the first three numeric parts. None when unavailable (script run,
    old exe).
    """
    try:
        version_dll = ctypes.WinDLL("version", use_last_error=True)
        size = version_dll.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(path, 0, size, data):
            return None
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version_dll.VerQueryValueW(
                data, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        # VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, FileVersionMS, FileVersionLS
        ffi = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint * 4)).contents
        if ffi[0] != 0xFEEF04BD:
            return None
        # Prefer the string FileVersion: it keeps semver pre-release
        # suffixes ("1.14.0-rc2") that the numeric FILEVERSION resource
        # cannot carry - without it an rc build reports itself as the
        # stable version and never upgrades to the final release.
        wptr = ctypes.c_void_p()
        wlen = ctypes.c_uint()
        if version_dll.VerQueryValueW(
                data, "\\VarFileInfo\\Translation",
                ctypes.byref(wptr), ctypes.byref(wlen)) and wlen.value >= 4:
            lang_cp = ctypes.cast(wptr,
                                  ctypes.POINTER(ctypes.c_ushort * 2)).contents
            locale = "%04x%04x" % (lang_cp[0], lang_cp[1])
            sptr = ctypes.c_void_p()
            slen = ctypes.c_uint()
            if version_dll.VerQueryValueW(
                    data, "\\StringFileInfo\\%s\\FileVersion" % locale,
                    ctypes.byref(sptr), ctypes.byref(slen)) and slen.value:
                s = ctypes.wstring_at(sptr, slen.value).split("\x00")[0].strip()
                if s:
                    return s
        ms, ls = ffi[2], ffi[3]
        parts = (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        return ".".join(str(x) for x in parts[:3])
    except Exception:
        return None


def _own_exe_fullpath():
    """Full path of our own exe when frozen, else None."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def effective_version():
    """Version the updater compares against: the installed exe's embedded
    VERSIONINFO when available (survives the exe being replaced by an
    update), otherwise APP_VERSION from this build.
    """
    path = _own_exe_fullpath()
    if path:
        v = read_file_version(path)
        if v:
            return v
    return APP_VERSION


# All disks, no reliability counters (fast, ~1 s). Used by the debug list.
PS_LIST = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$d=Get-PhysicalDisk;"
    "$r=@(); foreach($x in $d){"
    "$r+=[pscustomobject]@{model=$x.Model;friendly=$x.FriendlyName;"
    "media=$x.MediaType;bus=$x.BusType}};"
    "$r|ConvertTo-Json -Compress"
)

# Internal SSDs only, with reliability counters. IMPORTANT: the USB filter
# must run BEFORE Get-StorageReliabilityCounter -- some USB bridge chips
# (e.g. ASMedia ASM236X) both report a bogus temperature (0) AND block the
# counter read for ~40 seconds, which would blow the 30 s subprocess timeout.
PS_TEMPS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$d=Get-PhysicalDisk | Where-Object { $_.MediaType -eq 'SSD' -and $_.BusType -ne 'USB' };"
    "$r=@(); foreach($x in $d){"
    "$c=$x|Get-StorageReliabilityCounter;"
    "$r+=[pscustomobject]@{model=$x.Model;friendly=$x.FriendlyName;"
    "media=$x.MediaType;bus=$x.BusType;temp=$c.Temperature;"
    "wear=$c.Wear;readErr=$c.ReadErrorsTotal;"
    "undef=$c.ReadErrorsUncorrected}};"
    "$r|ConvertTo-Json -Compress"
)


def _run_powershell(cmd):
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.strip()
    except Exception:
        return ""


def _safe_temp(raw):
    """Convert a raw PowerShell value to a sane int temperature or None."""
    try:
        temp = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        temp = None
    # Sanity check: a real SMART temperature stays within this range.
    # Bridge chips on USB enclosures can report garbage (e.g. 0 or 65535).
    if temp is not None and not TEMP_MIN <= temp <= TEMP_MAX:
        temp = None
    return temp


def _parse_disks(out, with_temp):
    """Parse ConvertTo-Json output into a list of disk dicts."""
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    disks = []
    for item in data:
        disk = {
            "model": item.get("model") or item.get("friendly") or "SSD",
            "media": item.get("media") or "?",
            "bus": item.get("bus") or "?",
            "temp": _safe_temp(item.get("temp")) if with_temp else None,
            "wear": _safe_small_int(item.get("wear")),
            "read_errors": _safe_small_int(item.get("readErr")),
            "unfixed_errors": _safe_small_int(item.get("undef")),
        }
        disks.append(disk)
    return disks


def _safe_small_int(raw):
    """Non-negative int or None (SMART counters must never go below 0)."""
    try:
        v = int(raw)
        return v if 0 <= v <= 10 ** 12 else None
    except (TypeError, ValueError):
        return None


def health_flags(disk):
    """List of human-readable SMART concerns for a disk (empty = healthy)."""
    flags = []
    wear = disk.get("wear")
    if wear is not None and wear >= 90:
        flags.append(tr("health.wear_high", wear=wear))
    elif wear is not None and wear >= 75:
        flags.append(tr("health.wear_used", wear=wear))
    unfixed = disk.get("unfixed_errors") or 0
    total = disk.get("read_errors") or 0
    if unfixed:
        flags.append(tr("health.read_errors", unfixed=unfixed))
    elif total >= 100:
        flags.append(tr("health.read_errors_fixed", total=total))
    return flags


def wear_band(wear):
    """0 = ok, 1 = used (>=75%), 2 = high (>=90%) - mirrors health_flags."""
    if wear is None:
        return 0
    if wear >= 90:
        return 2
    if wear >= 75:
        return 1
    return 0


def wear_crossed(prev, cur):
    """True when cur newly reaches a worse wear band than prev."""
    return prev is not None and cur is not None and wear_band(cur) > wear_band(prev)


def unfixed_new_errors(prev, cur):
    """How many NEW uncorrected errors appeared (0 when unknown/none)."""
    if prev is None or cur is None:
        return 0
    return max(0, cur - prev)


def smart_watch_changes(temps, state):
    """Pure core of the SMART watchdog (mirror of alert_state).

    Compares the fresh reading against the persisted per-model state and
    returns (new_state, fired_messages). The state updates for every
    counter that is readable, but a message only fires for NEW damage:
    a rising uncorrected-error counter or wear newly crossing into a
    worse band (>=75%, >=90%).
    """
    state = dict(state)
    fired = []
    for t in temps:
        model = t.get("model", "SSD")
        prev = state.get(model, {})
        unfixed = t.get("unfixed_errors")
        wear = t.get("wear")
        if unfixed is not None:
            new_err = unfixed_new_errors(prev.get("unfixed"), unfixed)
            if new_err:
                fired.append(tr("smart.new_errors", n=new_err, model=model))
            state[model] = dict(prev, unfixed=unfixed)
        if wear is not None:
            if wear_crossed(prev.get("wear"), wear):
                fired.append(tr("smart.wear_band", wear=wear, model=model))
            state[model] = dict(state.get(model, prev), wear=wear)
    return state, fired


SMART_ALERT_COOLDOWN_MINUTES = 30

# pre-emptive "wear rising fast" watch (from the daily health log)
WEAR_SLOPE_WINDOW_DAYS = 7
WEAR_SLOPE_PCT_PER_WEEK = 2.0        # percentage points per window
WEAR_SLOPE_COOLDOWN_DAYS = 7         # re-alert at most weekly per disk


def count_log_events(event, days=7, log_path=None, now=None):
    """How many times ``event`` appears in the app log within ``days``.

    Counts ``<ts> INFO <event>`` occurrences (any level actually) in the
    rotating log file - best effort: unreadable/missing log counts as 0.
    """
    path = log_path or LOG_FILE
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    n = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if event not in line:
                    continue
                m = LOG_LINE_TS.match(line)
                if not m:
                    continue
                try:
                    ts = time.mktime(time.strptime(m.group(1),
                                                  "%Y-%m-%d %H:%M:%S,%f"))
                except ValueError:
                    continue
                if ts >= cutoff:
                    n += 1
    except OSError:
        pass
    return n


def format_week_stats(stats):
    """Render a week_stats() dict into display lines (shared by the
    Settings Stats tab and the tray Stats window)."""
    lines = [tr("stats.alerts", n=stats["alerts"]),
             tr("stats.smart_alerts", n=stats["smart_alerts"]), ""]
    for model, d in stats["per_disk"].items():
        lines.append(model)
        lines.append(tr("stats.disk_line", mn=f"{d['min']:.0f}",
                        av=f"{d['avg']:.1f}", mx=f"{d['max']:.0f}",
                        wear=(f"{d['wear']}%" if d["wear"] is not None
                              else "-")))
    lines.append("")
    lines.append(tr("stats.window"))
    return lines


def week_stats(rows, state=None):
    """Usage summary for the Stats view.

    Per model and overall min/avg/max temperature over the last 7 days
    (from the daily health log) plus ``alerts`` (overheat toasts) and
    ``smart_alerts`` (SMART/wear toasts) counted from the event log.
    """
    import datetime as _dt
    cutoff = (_dt.datetime.now()
              - _dt.timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [r for r in rows if r.get("date", "") >= cutoff]
    alerts = count_log_events("overheat_alert", days=7)
    smart_alerts = count_log_events("smart_alert", days=7)
    per = {}
    for model in sorted({r.get("model") for r in recent if r.get("model")}):
        temps = [float(r["temp_c"]) for r in recent
                 if r.get("model") == model and r.get("temp_c")]
        wear = [int(r["wear_pct"]) for r in recent
                if r.get("model") == model
                and r.get("wear_pct") not in (None, "", "None")]
        if not temps:
            continue
        per[model] = {"min": min(temps), "avg": sum(temps) / len(temps),
                      "max": max(temps),
                      "wear": max(wear) if wear else None}
    return {"per_disk": per, "alerts": alerts, "smart_alerts": smart_alerts}


def wear_slope_alert(rows, now=None, state=None):
    """Pure pre-emptive wear-slope watch over the daily health log.

    Compares the max wear between the first and last day of the sliding
    window (default 7 days): a gain of WEAR_SLOPE_PCT_PER_WEEK or more
    warns BEFORE the 75/90 bands are reached. Deduplicated per disk for
    WEAR_SLOPE_COOLDOWN_DAYS via marks stored in the smart state file.
    Returns (message_or_None, new_state).
    """
    now = time.time() if now is None else now
    state = dict(state or {})
    marks = dict(state.get("_wear_slope", {}))
    alerts = []
    for model in sorted({r.get("model") for r in rows if r.get("model")}):
        series = [w for _d, _a, _l, _h, w
                  in trend_series(rows, model, days=WEAR_SLOPE_WINDOW_DAYS)]
        series = [w for w in series if w is not None]
        if len(series) < 3:
            continue
        gain = series[-1] - series[0]
        if gain < WEAR_SLOPE_PCT_PER_WEEK:
            continue
        if now - float(marks.get(model, 0)) < WEAR_SLOPE_COOLDOWN_DAYS * 86400:
            continue
        marks[model] = now
        alerts.append(tr("smart.wear_slope", model=model, gain=f"{gain:.0f}"))
    if not alerts:
        return None, state
    state["_wear_slope"] = marks
    return "\n".join(alerts), state
SMART_STATE_FILE = "smart_state.json"


def _smart_state_path(data_dir=None):
    return os.path.join(data_dir or DATA_DIR, SMART_STATE_FILE)


def load_smart_state(data_dir=None):
    """Last-seen SMART counters per model ({model: {wear, unfixed}}).

    Persisted so an app restart does not re-alert on errors that were
    already known. Corrupt/missing file -> empty dict.
    """
    try:
        with open(_smart_state_path(data_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_smart_state(state, data_dir=None):
    """Persist the last-seen SMART counters (best effort)."""
    try:
        with open(_smart_state_path(data_dir), "w", encoding="utf-8") as f:
            json.dump(state, f)
        return True
    except OSError:
        return False


HEALTH_LOG_FILE = os.path.join(DATA_DIR, "health_daily.csv")
HEALTH_TREND_DAYS = 30


def append_daily_health(temps, now=None, path=None):
    """Append one row per disk to health_daily.csv - at most once/day.

    Returns True when a row was appended. The date check is based on the
    last line already in the file, so repeated calls on the same day are
    cheap no-ops and the collection survives restarts.
    """
    path = path or HEALTH_LOG_FILE
    now = time.time() if now is None else now
    stamp = time.strftime("%Y-%m-%d", time.localtime(now))
    rows = []
    try:
        if os.path.isfile(path):
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
    except OSError:
        rows = []
    if rows and rows[-1].get("date") == stamp:
        return False
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not rows:
                w.writerow(["date", "time", "model", "bus",
                            "temp_c", "wear_pct", "read_errors",
                            "uncorrected"])
            hhmm = time.strftime("%H:%M", time.localtime(now))
            for t in temps:
                def _v(key):
                    val = t.get(key)
                    return "" if val is None else val
                w.writerow([stamp, hhmm, t.get("model", "SSD"),
                            t.get("bus", "?"), _v("temp"), _v("wear"),
                            _v("read_errors"), _v("unfixed_errors")])
        return True
    except OSError:
        return False


def load_daily_health(path=None):
    """health_daily.csv -> list of dict rows (oldest first), or []."""
    path = path or HEALTH_LOG_FILE
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError):
        return []


def trend_series(rows, model, days=HEALTH_TREND_DAYS):
    """Daily aggregates for one model: [(date, avg, min, max, max_wear)].

    Only the most recent ``days`` entries are returned so the trend view
    stays readable. Temperatures without data (""/None) are skipped.
    """
    per_day = {}
    for r in rows:
        if r.get("model") != model:
            continue
        try:
            temp = float(r["temp_c"])
        except (KeyError, TypeError, ValueError):
            continue
        wear = None
        try:
            wear = int(r["wear_pct"])
        except (KeyError, TypeError, ValueError):
            pass
        day = per_day.setdefault(r.get("date", ""),
                                 {"t": [], "wear": None})
        day["t"].append(temp)
        if wear is not None:
            day["wear"] = wear if day["wear"] is None else max(day["wear"], wear)
    out = []
    for date in sorted(per_day):
        ts = per_day[date]["t"]
        out.append((date, sum(ts) / len(ts), min(ts), max(ts),
                    per_day[date]["wear"]))
    return out[-days:]


WEEKLY_REPORT_DAYS = 7


def _svg(points, color, label, y_lo, y_hi):
    """One 404x170 SVG line chart from (index, value) pairs."""
    span = max(y_hi - y_lo, 1)
    pts = " ".join(
        f"{12 + i * 380 / max(n - 1, 1):.1f},"
        f"{150 - (v - y_lo) / span * 120:.1f}"
        for i, (n, v) in enumerate(points))
    return (f'<svg viewBox="0 0 404 170" width="404" height="170" role="img" '
            f'aria-label="{label}">\n'
            f'  <line x1="12" y1="150" x2="392" y2="150" stroke="#334155"/>\n'
            f'  <polyline fill="none" stroke="{color}" stroke-width="2" '
            f'points="{pts}"/>\n</svg>')


def build_weekly_report_html(rows, now=None):
    """Render the last 7 days of health_daily.csv as a standalone HTML.

    One section per disk model: an SVG line chart (daily avg temperature)
    and a compact wear/error table. Returns an empty string when there is
    no data for the window. The output has no external dependencies, so
    it opens correctly from any folder or is attachable to an email.
    """
    import datetime as _dt
    cutoff_str = (_dt.datetime.now()
                  - _dt.timedelta(days=WEEKLY_REPORT_DAYS)).strftime(
                      "%Y-%m-%d")
    models = []
    for r in rows:
        if r.get("model") and r["model"] not in models:
            models.append(r["model"])
    sections = []
    for model in models:
        series = trend_series(rows, model, days=WEEKLY_REPORT_DAYS)
        series = [s for s in series if s[0] >= cutoff_str]
        if not series:
            continue
        errs = {}
        for r in rows:
            if r.get("model") != model:
                continue
            try:
                n = int(r.get("uncorrected") or 0)
            except (TypeError, ValueError):
                n = 0
            d = r.get("date", "")
            if d >= cutoff_str:
                errs[d] = errs.get(d, 0) + n
        chart_temp = _svg([(i, avg) for i, (_d, avg, _lo, _hi, _w)
                           in enumerate(series)],
                          "#38bdf8", "daily average temperature", 25, 75)
        chart_wear = _svg([(i, w) for i, (_d, _a, _l, _h, w) in enumerate(series)
                           if w is not None] or [(0, 0)],
                          "#f59e0b", "wear percentage", 0, 100)
        chart_err = _svg([(i, min(errs.get(d, 0), 500))
                          for i, (d, *_rest) in enumerate(series)],
                         "#ef4444", "uncorrected read errors", 0, 500)
        wear_rows = "".join(
            f"<tr><td>{d}</td><td>{avg:.1f}</td><td>{lo:.0f}</td>"
            f"<td>{hi:.0f}</td><td>{w if w is not None else '-'}</td></tr>"
            for d, avg, lo, hi, w in series)
        sections.append(f"""
  <section>
    <h2>{model}</h2>
    <p class="muted">Temperature (°C)</p>
    {chart_temp}
    <p class="muted">Wear (%)</p>
    {chart_wear}
    <p class="muted">Uncorrected read errors (clipped at 500)</p>
    {chart_err}
    <table>
      <tr><th>date</th><th>avg °C</th><th>min</th><th>max</th>
          <th>wear %</th></tr>
      {wear_rows}
    </table>
  </section>""")
    if not sections:
        return ""
    comparison = multi_disk_comparison_html(rows)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SSD Health Weekly Report - {stamp}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; margin: 24px auto;
         max-width: 560px; color: #0f172a; }}
  h1 {{ font-size: 1.3rem; }}
  section {{ border: 1px solid #e2e8f0; border-radius: 10px;
            padding: 12px 16px; margin: 18px 0; }}
  h2 {{ font-size: 1.05rem; margin: 4px 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  th, td {{ border-bottom: 1px solid #e2e8f0; padding: 4px 6px;
           text-align: left; }}
  th {{ color: #64748b; font-weight: 600; }}
  .muted {{ color: #64748b; font-size: .85rem; }}
</style>
</head>
<body>
<h1>SSD Health Weekly Report</h1>
<p class="muted">Generated {stamp} · last {WEEKLY_REPORT_DAYS} days
· SSD Temperature Monitor</p>
{comparison}
{''.join(sections)}
</body>
</html>
"""


def open_weekly_report(parent=None):
    """Build the weekly HTML report, save it next to the other exports
    and open it in the default browser. Returns the path or None."""
    try:
        rows = load_daily_health()
        html = build_weekly_report_html(rows)
        if not html:
            return None
        out = os.path.join(DATA_DIR, WEEKLY_REPORT_FILE)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        os.startfile(out)  # default browser
        log_event("weekly_report_opened", rows=len(rows))
        return out
    except Exception:
        return None


WEEKLY_REPORT_FILE = "ssd_health_weekly.html"


def write_weekly_report(dest_dir=None):
    """Build + write the weekly HTML report into ``dest_dir`` (or DATA_DIR).

    Used by both the open button and the weekly auto-report. Returns the
    path or None when there is no data for the window.
    """
    html = build_weekly_report_html(load_daily_health())
    if not html:
        return None
    out = os.path.join(dest_dir or DATA_DIR, WEEKLY_REPORT_FILE)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    log_event("weekly_report_written", path=out)
    return out


def write_health_csv(dest_path):
    """Write the whole health_daily.csv content as an Excel-friendly CSV

    (UTF-8 BOM so Excel detects the encoding, CRLF line endings). Source
    columns are kept as-is; returns the path or None with no data.
    """
    rows = load_daily_health()
    if not rows:
        return None
    if str(dest_path).lower().endswith(".html"):
        dest_path = str(dest_path)[:-5] + ".csv"
    with open(dest_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\ufeff")
        f.write("sep=,\r\n")
        w = csv.writer(f, lineterminator="\r\n")
        cols = ["date", "time", "model", "bus", "temp_c", "wear_pct",
                "read_errors", "uncorrected"]
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])
    log_event("health_csv_exported", path=str(dest_path), rows=len(rows))
    return str(dest_path)


def multi_disk_comparison_html(rows, now=None):
    """One combined SVG chart with every disk's daily average temperature.

    Only shown when more than one disk has data. Uses the same 404x170
    frame as the per-disk charts; one polyline per model with its color
    from a fixed palette. Returns an empty string for <=1 disk.
    """
    import datetime as _dt
    cutoff = (_dt.datetime.now()
              - _dt.timedelta(days=WEEKLY_REPORT_DAYS)).strftime("%Y-%m-%d")
    models = []
    for r in rows:
        m_ = r.get("model")
        if m_ and m_ not in models:
            models.append(m_)
    if len(models) < 2:
        return ""
    palette = ("#38bdf8", "#f59e0b", "#ef4444", "#22c55e", "#a78bfa",
               "#f472b6", "#2dd4bf", "#fbbf24")
    lines = []
    legend = []
    for i, model in enumerate(models):
        series = [s for s in trend_series(rows, model, days=WEEKLY_REPORT_DAYS)
                  if s[0] >= cutoff]
        if not series:
            continue
        pts = " ".join(
            f"{12 + j * 380 / max(len(series) - 1, 1):.1f},"
            f"{150 - max(0, min(100, (avg - 25) / 50 * 120)):.1f}"
            for j, (_d, avg, _lo, _hi, _w) in enumerate(series))
        color = palette[i % len(palette)]
        lines.append(f'<polyline fill="none" stroke="{color}" '
                     f'stroke-width="2" points="{pts}"/>')
        legend.append(f'<span style="color:{color}">&#9632;</span> {model}')
    if len(legend) < 2:
        return ""
    body = "".join(f'  {ln}\n' for ln in lines)
    return ('<svg viewBox="0 0 404 170" width="404" height="170" '
            'role="img" aria-label="all disks daily average temperature">\n'
            '  <line x1="12" y1="150" x2="392" y2="150" stroke="#334155"/>\n'
            f'{body}</svg>\n<p class="muted">{" &nbsp; ".join(legend)}</p>\n')


def save_weekly_report_as(parent=None):
    """Ask for a destination and write the weekly report there (no open).
    Returns the chosen path or None when cancelled/empty."""
    try:
        from tkinter import filedialog
        rows = load_daily_health()
        html = build_weekly_report_html(rows)
        if not html:
            return None
        name = time.strftime("ssd_health_weekly_%Y%m%d.html")
        path = filedialog.asksaveasfilename(
            parent=parent, defaultextension=".html",
            filetypes=(("HTML", "*.html"), ("All files", "*.*")),
            initialfile=name)
        if not path:
            return None
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log_event("weekly_report_exported", path=path)
        return path
    except Exception:
        return None


def list_all_disks():
    """Return {model, media, bus, temp} for every physical disk (debug).

    Temperatures are only queried for internal SSDs -- reading the
    reliability counter of USB-attached disks can block for tens of
    seconds on some bridge chips, so their temp stays None.
    """
    temps = {d["model"]: d["temp"] for d in read_temps()}
    disks = _parse_disks(_run_powershell(PS_LIST), with_temp=False)
    for d in disks:
        d["temp"] = temps.get(d["model"])
    return disks


def is_internal_ssd(disk):
    """True for internal SSDs only.

    USB card readers / enclosures commonly report MediaType 'SSD' while
    their bridge chip returns invalid temperature values -> exclude USB.
    """
    return (disk["media"] or "").lower() == "ssd" and (disk["bus"] or "").lower() != "usb"


def read_temps():
    """Return list of {model, temp} dicts for internal SSDs only.

    temp is int (C) or None. Uses the pre-filtered PS_TEMPS query so a
    USB bridge chip can never stall the polling loop, and applies
    is_internal_ssd() again client-side as a safety net.
    """
    return [
        {"model": d["model"], "temp": d["temp"],
         "wear": d.get("wear"), "read_errors": d.get("read_errors"),
         "unfixed_errors": d.get("unfixed_errors")}
        for d in _parse_disks(_run_powershell(PS_TEMPS), with_temp=True)
        if is_internal_ssd(d)
    ]


def temp_color(temp):
    if temp is None:
        return UNKNOWN
    if temp >= 65:
        return RED
    if temp >= 51:
        return ORANGE
    return GREEN


def temp_zone_bands(y_lo=GRAPH_Y_LO, y_hi=GRAPH_Y_HI):
    """Temperature zones as [(zlo, zhi, hexcolor), ...] clipped to a range.

    Same thresholds as temp_color(): green below 51, orange 51-64,
    red from 65. Used to paint the background zone bands of the graph
    windows (drawn faintly, under the data line).
    """
    zones = ((0, 51, GREEN), (51, 65, ORANGE), (65, 10_000, RED))
    out = []
    for zlo, zhi, color in zones:
        lo, hi = max(zlo, y_lo), min(zhi, y_hi)
        if lo < hi:
            out.append((lo, hi, color))
    return out


def _parse_version(v):
    """'v1.2.3-rc2' -> comparable tuple with semver-style pre-release order.

    Plain releases sort after every pre-release of the same core version
    (release flag 1 vs 0) and rc numbers sort within the pre-releases:
    ``1.14.0-rc1 < 1.14.0-rc2 < 1.14.0`` while ``1.13.9 < 1.14.0-rc1``.
    """
    try:
        core, _, pre = str(v).strip().lstrip("vV").partition("-")
        nums = tuple(int(x) for x in core.split(".")[:3])
        nums += (0,) * (3 - len(nums))
        if pre:
            digits = "".join(ch for ch in pre if ch.isdigit())
            return nums + (0, int(digits or 0))
        return nums + (1, 0)
    except ValueError:
        return (0,)


def is_newer_version(remote, local=None):
    """True when the remote version string is strictly newer than ours.

    local defaults to the installed exe's embedded version (effective
    version), falling back to APP_VERSION.
    """
    if local is None:
        local = effective_version()
    try:
        return _parse_version(remote) > _parse_version(local)
    except Exception:
        return False


def remember_broken_version(version):
    """Record a version that had to be rolled back so the updater skips it.

    Without this the next auto-check would offer the same broken release
    again, rolling back over and over. Stored one-per-line in
    ROLLBACK_BROKEN_FILE next to the config (survives reinstalls of the
    marker files, which are one-shot).
    """
    try:
        path = _rollback_marker_path(ROLLBACK_BROKEN_FILE)
        bad = str(version or "").strip()
        if not bad:
            return
        lines = []
        try:
            with open(path) as f:
                lines = [l.strip() for l in f if l.strip()]
        except OSError:
            pass
        if bad not in lines:
            lines.append(bad)
            with open(path, "w") as f:
                f.write("\n".join(lines[-10:]) + "\n")
    except Exception:
        pass


def version_is_broken(version):
    """True when remember_broken_version() recorded this tag before."""
    try:
        with open(_rollback_marker_path(ROLLBACK_BROKEN_FILE)) as f:
            return str(version or "").strip() in {l.strip() for l in f if l.strip()}
    except OSError:
        return False


def _is_prerelease(release):
    """True when the release/tag looks like a pre-release (v1.2.3-rc1...)."""
    tag = str(release.get("tag_name") or release.get("name") or "")
    return "-" in tag


def select_release_asset(release, prefer_prerelease=False):
    """Pick the setup exe asset from a GitHub release dict.

    Prefers ssd_temp_monitor_setup_*.exe and falls back to any .exe that
    is not a portable build. With prefer_prerelease=True the app follows
    the pre-release channel: draft/pre-release releases are also accepted.
    Returns (asset_url, version) or (None, None).
    """
    if not isinstance(release, dict):
        return None, None
    if not prefer_prerelease and _is_prerelease(release):
        return None, None
    assets = release.get("assets") or []
    version = release.get("tag_name") or release.get("name") or ""
    setup = portable = None
    for asset in assets:
        if asset.get("state") == "uploaded" and str(asset.get("name", "")).lower().endswith(".exe"):
            name = asset["name"].lower()
            if "setup" in name and setup is None:
                setup = asset
            elif "setup" not in name and portable is None:
                portable = asset
    chosen = setup or portable
    if not chosen:
        return None, None
    return chosen.get("browser_download_url"), str(version)


def fetch_latest_release(repo, include_prereleases=False):
    """Query the GitHub Releases API. Returns a release dict or None.

    Default asks for the latest *stable* release. With
    include_prereleases=True the most recent release of any kind wins
    (that is what a pre-release channel wants). Network and API errors
    are swallowed and reported as 'no release' so a missing/broken
    connection can never break the poll loop.
    """
    if include_prereleases:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=1"
    else:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ssd-temp-monitor",
            })
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                return data[0] if data else None
            return data
    except Exception:
        return None


def sha256_hex(data):
    """SHA-256 of bytes as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def parse_checksums(text):
    """Parse a SHA256SUMS.txt file into {filename: lowercase-hex-hash}.

    Accepts standard ``<hash>  <name>`` lines (two spaces, sha256sum
    compatible) including the ``*<name>`` binary-mode marker; junk lines
    are ignored.
    """
    result = {}
    for line in str(text).splitlines():
        line = line.strip()
        if "  " not in line:
            continue
        hash_part, _, name = line.partition("  ")
        hash_part = hash_part.strip().lower()
        name = name.strip().lstrip("*")
        if len(hash_part) == 64 and name:
            result[name] = hash_part
    return result


def verify_asset(data, sums_text, filename):
    """True when *data* matches the hash recorded for *filename*."""
    expected = parse_checksums(sums_text).get(filename)
    if not expected:
        return False
    return sha256_hex(data) == expected


def build_update_shim(installer_path, app_exe_path=None, restart_path=None):
    """Write a tiny cmd that waits for our processes to exit, runs the
    installer, then relaunches the app.

    Why: setup.iss declares AppMutex, so the installer aborts (exit code 1,
    silently in /VERYSILENT) whenever the tray app is still holding the
    mutex. /CLOSEAPPLICATIONS does not help because Inno checks AppMutex
    before its close-app logic, and a silent install skips the postinstall
    launch — so the shim relaunches the updated app itself.
    The shim exits with the installer's exit code.
    """
    app = app_exe_path or _own_exe_path()
    watchdog_path, watchdog_line = build_rollback_watchdog_lines()
    fd, path = tempfile.mkstemp(prefix="ssd_update_", suffix=".cmd")
    with os.fdopen(fd, "w") as f:
        f.write(
            "@echo off\r\n"
            "rem SSD Temperature Monitor update shim: wait for the app to\r\n"
            "rem exit, then run the installer (AppMutex must be free).\r\n"
            "rem Clear PyInstaller onefile env vars: a relaunched app that\r\n"
            "rem inherits _MEIPASS2 would skip extraction and reuse THIS\r\n"
            "rem shim's already-deleted temp dir -> 'Failed to load Python\r\n"
            "rem DLL ... _MEIxxxx\\python3xx.dll'\r\n"
            'set "_MEIPASS2="\r\n'
            'set "_PYI_APPLICATION_HOME_DIR="\r\n'
            'set "_PYI_ARCHIVE_FILE="\r\n'
            'set "_PYI_PARENT_PROCESS_LEVEL="\r\n'
            'set "_PYI_SPLASH_IPC="\r\n'
            "            :wait\r\n"
            # absolute paths: never resolve `find` to GNU find from a
            # Git-bash PATH, which would silently break the pipeline
            # (%% -> literal % for the cmd %SystemRoot% variable).
            # tasklist's IMAGENAME filter matches the BARE file name only -
            # a full path filter matches nothing and the wait loop would
            # spin forever (found by the E2E rollback test)
            '%%SystemRoot%%\\System32\\tasklist.exe /FI "IMAGENAME eq %s" 2>nul '
            '| %%SystemRoot%%\\System32\\find.exe /I "%s" >nul '
            % (os.path.basename(app), os.path.basename(app))
            + "&& (%SystemRoot%\\System32\\ping.exe -n 1 127.0.0.1 >nul "
              "& goto wait)\r\n"
            "rem hand over to the installer; /CLOSEAPPLICATIONS makes it\r\n"
            "rem close this app and restart it afterwards (RestartManager)\r\n"
            + ('"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART '
               "/CLOSEAPPLICATIONS /RESTARTAPPLICATIONS\r\n" % installer_path)
            # A silent install skips the postinstall launch, so bring the
            # updated app back ourselves (only when the install succeeded).
            # launch DETACHED: the shim's cmd parent exits right after
            # `start`, and a PyInstaller 6.22+ onefile child validates its
            # parent process and dies with "Security validation failure:
            # invalid originating onefile parent process (PID not found)"
            # when that parent is already gone. An explorer-relaunch has no
            # such parent window at all.
            + (f'if not errorlevel 1 start "" /B explorer.exe "{restart_path}"\r\n'
               if restart_path else "")
            # rollback: launch the detached watchdog (it lingers in the
            # background after this shim exits; a successful update does
            # NOT wait for it - the watchdog deletes itself later)
            + watchdog_line
            + "exit /b %ERRORLEVEL%\r\n"
        )
    return path


def _own_restart_path():
    """Full path to relaunch the frozen exe (None for source runs)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def _own_exe_path():
    """Path of our own exe (frozen) or an empty sentinel."""
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable)
    return "ssd_temp_monitor.exe"


def wait_and_install(shim_path, timeout=90):
    """Run the update shim and wait (bounded) for the whole update to
    finish. Returns the shim's exit code (the installer's), or None.
    """
    try:
        proc = subprocess.run(
            ["cmd", "/c", shim_path],
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=timeout,
        )
        return proc.returncode
    except Exception:
        return None


# ---- automatic rollback --------------------------------------------------
#
# Before an update installs, the running app copies its own exe to
# ssd_temp_monitor.prev.exe and drops a pending marker. The update shim
# waits ROLLBACK_GRACE_SECONDS after the installer finishes; if the new
# app booted successfully it calls begin_healthy_session() which deletes
# marker+backup. If the marker survives that long the shim assumes the
# new exe cannot start, kills it, puts the old exe back and relaunches.
# The relaunched OLD app also sees the marker and tells the user what
# happened (the shim cannot show UI itself - it runs without a desktop
# interaction context).

ROLLBACK_BACKUP_NAME = "ssd_temp_monitor.prev.exe"
ROLLBACK_PENDING = "update_pending.marker"
ROLLBACK_REPORTED = "update_rollback_reported.marker"
ROLLBACK_BROKEN_FILE = "update_broken_versions.txt"
ROLLBACK_WATCHDOG_NAME = "ssd_rollback_watchdog.cmd"
ROLLBACK_GRACE_SECONDS = 90
# A REPORTED marker older than this is a leftover from an earlier session
# (e.g. a test or an update whose relaunch was interrupted) - it must NOT
# make a freshly installed, healthy version blacklist itself.
ROLLBACK_REPORT_STALE_SECONDS = 180


def _rollback_backup_path():
    return os.path.join(os.path.dirname(os.path.abspath(sys.executable))
                        if getattr(sys, "frozen", False) else os.getcwd(),
                        ROLLBACK_BACKUP_NAME)


def _rollback_marker_path(name):
    return os.path.join(DATA_DIR, name)


def stage_backup_for_rollback(app_exe=None, data_dir=None,
                              target_version=None):
    """Copy the current exe + drop the pending marker before an update.

    Best effort: any failure only disables rollback for this update, it
    must never block the update itself. Returns True when the safety net
    is in place. The pending marker records the version being installed
    (target_version) so a later rollback can blacklist exactly that
    release. (app_exe/data_dir are injectable for tests.)
    """
    try:
        exe = app_exe or (sys.executable if getattr(sys, "frozen", False) else None)
        if not exe or not os.path.isfile(exe):
            return False
        dest = (_rollback_backup_path() if data_dir is None
                else os.path.join(os.path.dirname(exe), ROLLBACK_BACKUP_NAME))
        shutil.copyfile(exe, dest)
        marker_dir = data_dir or DATA_DIR
        os.makedirs(marker_dir, exist_ok=True)
        with open(os.path.join(marker_dir, ROLLBACK_PENDING), "w") as f:
            f.write(str(target_version or ""))
        log_event("rollback_backup_staged", version=effective_version(),
                  target=target_version)
        return True
    except Exception:
        return False


def begin_healthy_session():
    """Called once at startup: discard the update safety net.

    Reaching this point proves the new exe boots, so the pending marker
    and the backup copy are no longer needed. If the marker is here
    because the shim already rolled us back (fresh REPORTED marker),
    show the rollback notice instead and ask the updater to skip this
    broken release. A STALE REPORTED marker (older than
    ROLLBACK_REPORT_STALE_SECONDS) is a leftover from an earlier session:
    it is cleaned up and the normal healthy path continues - otherwise a
    healthy boot after an update would blacklist the version it just
    installed and pop a bogus rollback warning.
    """
    reported = _rollback_marker_path(ROLLBACK_REPORTED)
    if os.path.isfile(reported):
        try:
            age = time.time() - os.path.getmtime(reported)
        except OSError:
            age = 0  # unreadable mtime: treat as fresh (safe default)
        if age <= ROLLBACK_REPORT_STALE_SECONDS:
            _notify_rollback_reported()
            return
        log_event("rollback_report_stale", age_seconds=int(age))
        try:
            os.remove(reported)
        except OSError:
            pass
    if not os.path.isfile(_rollback_marker_path(ROLLBACK_PENDING)):
        return
    try:
        os.remove(_rollback_marker_path(ROLLBACK_PENDING))
    except OSError:
        pass
    try:
        os.remove(_rollback_backup_path())
    except OSError:
        pass
    log_event("update_session_healthy", version=effective_version())


def _notify_rollback_reported():
    """The shim rolled us back and left the report marker: blacklist the
    version that failed, tell the user once, then clear both markers."""
    broken = ""
    try:
        with open(_rollback_marker_path(ROLLBACK_PENDING)) as f:
            broken = f.read().strip()
    except OSError:
        pass
    if broken:
        remember_broken_version(broken)
    for name in (ROLLBACK_REPORTED, ROLLBACK_PENDING):
        try:
            os.remove(_rollback_marker_path(name))
        except OSError:
            pass
    log_event("update_rolled_back", version=broken)
    threading.Thread(
        target=lambda: ctypes.windll.user32.MessageBoxW(
            None,
            tr("rollback.body"),
            tr("rollback.title"),
            0x30 | 0x40000 | 0x10000,  # warning | topmost | foreground
        ), daemon=True).start()


def build_rollback_watchdog_lines():
    """A detached watchdog cmd, written next to the installed exe.

    The update shim merely STARTS it (start "" /B) and exits right after
    the installer - so a successful update returns immediately, while the
    watchdog lingers in the background. It sleeps ROLLBACK_GRACE_SECONDS,
    then: if ROLLBACK_PENDING still exists the new exe never reported a
    healthy boot -> kill it, restore the backup, relaunch and drop the
    report marker so the old app explains to the user. Either way the
    watchdog deletes its own file at the end. Returns (path, shim_line)
    or (None, "") when rollback is impossible (no backup staged).
    """
    app = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None
    backup = _rollback_backup_path()
    marker = _rollback_marker_path(ROLLBACK_PENDING)
    reported = _rollback_marker_path(ROLLBACK_REPORTED)
    if not app or not os.path.isfile(backup):
        return None, ""
    app_dir = os.path.dirname(app)
    watchdog = os.path.join(app_dir, ROLLBACK_WATCHDOG_NAME)
    base = os.path.basename(app)
    sys32 = "%SystemRoot%\\System32"
    crlf = "\r\n"
    script = (
        "@echo off" + crlf
        + "rem SSD Temperature Monitor rollback watchdog (self-deleting)" + crlf
        # ping-based sleep: timeout.exe aborts with "Input redirection is
        # not supported" when stdin is not a console (piped/redirected
        # launches), which would skip the grace delay entirely and roll
        # back a healthy update - ping works with any stdin
        + f'"{sys32}\\ping.exe" -n {ROLLBACK_GRACE_SECONDS + 1} 127.0.0.1 >nul' + crlf
        + f'if not exist "{marker}" goto done' + crlf
        + f'"{sys32}\\taskkill.exe" /F /IM {base} >nul 2>&1' + crlf
        + f'"{sys32}\\ping.exe" -n 2 127.0.0.1 >nul' + crlf
        + f'copy /Y "{backup}" "{app}" >nul' + crlf
        + f'del /Q "{backup}" >nul 2>&1' + crlf
        + f'type NUL > "{reported}"' + crlf
        + 'start "" /B explorer.exe "' + app + '"' + crlf
        + ":done" + crlf
        + '(goto) 2>nul & del /Q "%~f0"' + crlf
    )
    try:
        with open(watchdog, "w", encoding="ascii") as f:
            f.write(script)
    except OSError:
        return None, ""
    return watchdog, f'start "" /B "{watchdog}"{crlf}'


def build_restore_shim(backup_path, app_exe):
    """Tiny cmd for the menu-driven "restore previous version": wait for
    this app to exit, copy the backup over the current exe and relaunch.
    Same wait/clear-env/explorer-relaunch rules as the update shim."""
    fd, path = tempfile.mkstemp(prefix="ssd_restore_", suffix=".cmd")
    with os.fdopen(fd, "w") as f:
        f.write(
            "@echo off\r\n"
            "rem SSD Temperature Monitor restore shim\r\n"
            'set "_MEIPASS2="\r\n'
            'set "_PYI_APPLICATION_HOME_DIR="\r\n'
            'set "_PYI_ARCHIVE_FILE="\r\n'
            'set "_PYI_PARENT_PROCESS_LEVEL="\r\n'
            'set "_PYI_SPLASH_IPC="\r\n'
            ":wait\r\n"
            # tasklist's IMAGENAME filter matches the bare file name only
            '%%SystemRoot%%\\System32\\tasklist.exe /FI "IMAGENAME eq %s" 2>nul '
            '| %%SystemRoot%%\\System32\\find.exe /I "%s" >nul '
            % (os.path.basename(app_exe), os.path.basename(app_exe))
            + "&& (%SystemRoot%\\System32\\ping.exe -n 1 127.0.0.1 >nul "
              "& goto wait)\r\n"
            f'copy /Y "{backup_path}" "{app_exe}" >nul\r\n'
            f'del /Q "{backup_path}" >nul 2>&1\r\n'
            f'start "" /B explorer.exe "{app_exe}"\r\n'
            "exit /b 0\r\n"
        )
    return path


def heal_autostart_value(reg=None):
    """Fix a Run key left pointing at a source interpreter (pythonw)."""
    if getattr(sys, "frozen", False):
        return False
    try:
        import winreg
        reg = reg or winreg
        with reg.OpenKey(reg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            value, _ = reg.QueryValueEx(key, AUTOSTART_VALUE)
        point = _autostart_entry_point()
        if os.path.normpath(value.strip('"').strip()) != os.path.normpath(point):
            set_autostart(True, reg)
            log_event("autostart_healed", old=value, new=point)
            return True
    except OSError:
        pass
    return False


def cleanup_stale_mei(min_age_seconds=60):
    """Delete leftover PyInstaller onefile temp dirs from dead processes.

    A crashed or killed onefile run leaves ``_MEIxxxxxx`` behind in %TEMP%
    forever (pyinstaller/pyinstaller#5518). Liveness probe: a directory
    whose DLLs are still mapped by a running process cannot be renamed
    (sharing violation), so "rename succeeded" proves the owner is gone.
    Our own extraction dir (``sys._MEIPASS``) and dirs younger than
    ``min_age_seconds`` (an app may be starting up right now) are kept.
    Source runs are a no-op. Never raises.
    """
    if not getattr(sys, "frozen", False):
        return
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if not temp:
        return
    own = os.path.normcase(os.path.abspath(
        getattr(sys, "_MEIPASS", "") or os.devnull))
    try:
        now = time.time()
        for name in os.listdir(temp):
            if not (name.startswith("_MEI") and len(name) > 4
                    and name[4:].isdigit()):
                continue
            path = os.path.join(temp, name)
            if os.path.normcase(os.path.abspath(path)) == own:
                continue  # that is us - definitely alive
            try:
                if now - os.path.getmtime(path) < min_age_seconds:
                    continue  # brand new: its owner may be starting up
            except OSError:
                continue
            staged = path + "_stale"
            try:
                os.rename(path, staged)  # fails while a process holds it
            except OSError:
                continue
            shutil.rmtree(staged, ignore_errors=True)
    except OSError:
        pass


def run_update_selftest():
    """Exercise the update pipeline's pure helpers; returns (ok, lines).

    Simulates the exact semantics the real updater depends on: version
    comparison, release-asset selection, SHA-256 verification and the
    shim contract (env isolation + detached relaunch). Never touches the
    network or runs anything - safe to click at any time.
    """
    checks = []

    def check(name, fn):
        try:
            checks.append((name, bool(fn()), ""))
        except Exception as exc:  # a failing probe is a failed check
            checks.append((name, False, f"{type(exc).__name__}: {exc}"))

    # 1. version comparison drives "is there an update?"
    check("version: 1.13.0 > 1.12.1",
          lambda: is_newer_version("1.13.0", "1.12.1"))
    check("version: not newer 1.12.1 > 1.12.1",
          lambda: not is_newer_version("1.12.1", "1.12.1"))
    check("version: 1.10.0 > 1.9.0",
          lambda: is_newer_version("1.10.0", "1.9.0"))
    check("version: release beats its rc (semver)",
          lambda: is_newer_version("1.14.0", "1.14.0-rc1")
          and is_newer_version("1.14.0-rc2", "1.14.0-rc1")
          and not is_newer_version("1.14.0-rc1", "1.14.0"))

    # 2. asset selection: stable channel must reject pre-releases
    rel = {"tag_name": "v1.13.0", "prerelease": False,
           "draft": False,
           "assets": [{"name": "ssd_temp_monitor_setup_1.13.0.exe",
                       "state": "uploaded",
                       "browser_download_url":
                           "https://example.invalid/setup_1.13.0.exe"}]}
    check("asset: picks setup exe",
          lambda: select_release_asset(rel)[1] == "v1.13.0")
    rel_pre = dict(rel, tag_name="v1.14.0-rc1", prerelease=True)
    check("asset: stable skips pre-release",
          lambda: select_release_asset(rel_pre) == (None, None))

    # 3. checksum gate: the only thing standing between a corrupted
    #    download and Program Files
    good = sha256_hex(b"data")
    sums = good + "  setup.exe\n"
    check("checksum: matching hash passes",
          lambda: verify_asset(b"data", sums, "setup.exe"))
    check("checksum: wrong hash fails",
          lambda: not verify_asset(b"other", sums, "setup.exe"))
    check("checksum: unknown file fails",
          lambda: not verify_asset(b"data", sums, "missing.exe"))

    # 4. shim contract: env isolation (the v1.12.1 _MEIPASS2 fix) and the
    #    detached relaunch (the v1.12.0 parent-validation fix)
    shim = build_update_shim("C:\\setup.exe", restart_path="C:\\app.exe")
    try:
        with open(shim, encoding="utf-8") as f:
            body = f.read()
        check("shim: clears _MEIPASS2",
              lambda: 'set "_MEIPASS2="' in body)
        check("shim: clears _PYI_* vars",
              lambda: body.count('set "_PYI_') >= 4)
        check("shim: detached relaunch via explorer",
              lambda: 'explorer.exe' in body and "/B" in body)
        check("shim: waits for app exit before install",
              lambda: "tasklist" in body and ":wait" in body)
    finally:
        try:
            os.remove(shim)
        except OSError:
            pass

    failed = [c for c in checks if not c[1]]
    return not failed, (checks, failed)


def load_geometry():
    """Saved {window: "WxH+x+y"} sizes, or an empty dict. Never raises."""
    try:
        with open(GEOMETRY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_geometry(geo):
    """Merge window geometries into the geometry file. Never raises."""
    try:
        data = load_geometry()
        data.update(geo)
        os.makedirs(os.path.dirname(GEOMETRY_FILE), exist_ok=True)
        with open(GEOMETRY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except (OSError, TypeError, ValueError):
        pass


def geometry_of(win):
    """tk geometry string of a window, or "" when it is already gone."""
    try:
        return win.winfo_geometry()
    except Exception:
        return ""


def apply_geometry(win, key):
    """Restore a saved size/position for this window (best effort)."""
    geo = load_geometry().get(key)
    if geo:
        try:
            win.geometry(geo)
        except Exception:
            pass


def _tooltip_text(temps):
    """Tray tooltip: one line per disk, or a compact single line.

    compact: '65°C · 58°C (2 disks)' — full: 'Model: 65C | Model: 58C'.
    """
    if not temps:
        return "SSD Temperature Monitor - no data"
    if SETTINGS.get("compact_tooltip"):
        parts = [f"{t['temp']}°C" if t["temp"] is not None else "n/a"
                 for t in temps]
        if len(parts) > 1:
            return f"{' · '.join(parts)} ({len(parts)} disks)"
        return parts[0]
    return " | ".join(
        f"{t['model']}: {t['temp']}C" if t["temp"] is not None
        else f"{t['model']}: n/a"
        for t in temps)


AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "SSDTempMonitor"


def autostart_enabled(reg=None):
    """True when our HKCU Run value exists (the app runs elevated, but the
    key lives in the ORIGINAL user's hive via the installer/task).
    ``reg`` is injectable for tests (defaults to winreg).
    """
    try:
        import winreg
        reg = reg or winreg
        with reg.OpenKey(reg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            reg.QueryValueEx(key, AUTOSTART_VALUE)
        return True
    except (OSError, ImportError):
        return False


def _autostart_entry_point():
    """Command line the Run key should point at.

    A frozen exe registers itself; a SOURCE run registers the installed
    exe when it exists (registering pythonw here booted the machine into
    a source interpreter that silently blocked the real app's
    self-update by holding the single-instance mutex). When no installed
    exe exists the source entry point is registered anyway so dev setups
    keep working.
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    installed = os.path.join(r"C:\Program Files\SSD Temp Monitor",
                             "ssd_temp_monitor.exe")
    if os.path.isfile(installed):
        return installed
    return os.path.abspath(__file__)


def set_autostart(enable, reg=None):
    """Create/remove the HKCU Run value for the current user. Never raises.

    The app self-elevates, so sys.executable may resolve to the admin's
    copy - but HKCU under an elevated process still points at the same
    user hive in the common single-user case, which is what we target.
    """
    try:
        import winreg
        reg = reg or winreg
        with reg.OpenKey(reg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0,
                         reg.KEY_SET_VALUE) as key:
            if enable:
                reg.SetValueEx(key, AUTOSTART_VALUE, 0, reg.REG_SZ,
                               f'"{_autostart_entry_point()}"')
            else:
                try:
                    reg.DeleteValue(key, AUTOSTART_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def _open_target(target):
    """ShellExecute a target (the toast activation protocol)."""
    try:
        return ctypes.windll.shell32.ShellExecuteW(
            None, "open", target, None, None, 1) > 32
    except Exception:
        return False


def _register_protocol():
    """Register a ssdtempmon: protocol for toast activation (HKCU only).

    Windows toasts can only launch a registered target; we point the
    protocol at explorer-open of our own exe, which focuses the running
    tray app. Best effort: without the key, toasts simply do not react
    to clicks.
    """
    try:
        import winreg
        exe = sys.executable if getattr(sys, "frozen", False) \
            else os.path.abspath(__file__)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\ssdtempmon") as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ,
                              "URL:SSD Temp Monitor")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\ssdtempmon"
                              r"\shell\open\command") as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ,
                              f'explorer.exe "{exe}"')
        return True
    except OSError:
        return False


def notify_actionable(message, title):
    """Send a Windows toast that opens the app when clicked.

    Uses PowerShell's raw ToastXml API (no third-party dependency). The
    activation protocol is registered once; if the toast cannot be
    delivered we silently fall back to the pystray balloon.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Classes\ssdtempmon"):
            pass
    except OSError:
        _register_protocol()
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] | Out-Null;"
        "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent"
        "([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$t=$x.GetElementsByTagName('text');"
        f"$t.Item(0).AppendChild($x.CreateTextNode('{title}'))|Out-Null;"
        f"$t.Item(1).AppendChild($x.CreateTextNode('{message}'))|Out-Null;"
        "$x.DocumentElement.SetAttribute('launch','ssdtempmon:open');"
        "$n=[Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier"
        "('SSD Temp Monitor').Show($n)"
    )
    encoded = base64.b64encode(ps.encode("utf-16le")).decode("ascii")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def alert_state(hottest, since, last_alert, now):
    """Pure helper for the overheat notification logic.

    An alert fires when the hottest SSD has been >= ALERT_THRESHOLD for
    at least ALERT_SUSTAIN_SECONDS and the previous alert is older than
    ALERT_COOLDOWN_SECONDS. Returns (fire_alert, new_since); new_since is
    None whenever the temperature dropped below the threshold again.
    """
    if hottest is not None and hottest >= ALERT_THRESHOLD:
        since = since if since is not None else now
        fire = (now - since >= ALERT_SUSTAIN_SECONDS
                and now - last_alert >= ALERT_COOLDOWN_SECONDS)
        return fire, since
    return False, None


def _pill_text_color(pill):
    """Digit color chosen by the pill's luminance (WCAG-style contrast).

    On the medium green/orange pills dark digits reach ~9:1 contrast while
    white would only manage ~2:1; on the dark red pill white stays the
    readable choice. high-contrast black pill -> white digits.
    """
    rgb = pill
    if isinstance(rgb, str):  # "#rrggbb"
        rgb = tuple(int(rgb[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = rgb[:3]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (17, 17, 27, 255) if lum > 150 else (255, 255, 255, 255)


def _icon_font(size, text, family=None, style=None, scale=None):
    """Tray-digit font, auto-fit to the pill and then scaled by Settings.

    family/style select the typeface (see FONT_FAMILIES/_resolve_font_file).
    The base pixel size fills the pill's inner width; ``scale`` (50-150 %)
    grows or shrinks the digits afterwards, shrinking further when three
    digits would overflow. Results are cached per parameter set.
    """
    family = family or SETTINGS.get("icon_font", "Segoe UI")
    style = style or SETTINGS.get("icon_font_style", "bold")
    if scale is None:
        try:
            scale = int(SETTINGS.get("icon_digit_scale", 100))
        except (TypeError, ValueError):
            scale = 100
    scale = min(150, max(50, scale))
    font_file, _tk_name = _resolve_font_file(family, style)
    key = ("font", size, text, font_file, scale)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    px = int(size * (0.92 if size >= 48 else 0.86))
    margin = max(4, size // 12)
    max_w = size - 2 * margin
    try:
        font = ImageFont.truetype(font_file, px)
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
        if w > max_w:
            font = ImageFont.truetype(font_file,
                                      max(8, int(px * max_w / w)))
        if scale != 100:
            px2 = max(8, int(font.size * scale / 100))
            if px2 != font.size:
                font = ImageFont.truetype(font_file, px2)
                bbox = font.getbbox(text)
                w = bbox[2] - bbox[0]
                if w > max_w:   # scaled digits must still fit the pill
                    font = ImageFont.truetype(
                        font_file, max(8, int(px2 * max_w / w)))
    except OSError:
        font = ImageFont.load_default()
    _ICON_CACHE[key] = font
    return font


def _text_xy(size, bbox, text_h):
    """Center a text bbox inside a size x size icon."""
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return ((size - w) / 2 - bbox[0], (size - text_h) / 2 - bbox[1])


def make_icon(text, color, size=None, high_contrast=None):
    """Render the tray temperature icon.

    size: icon edge in px (default ICON_SIZE). The colored pill fills
    nearly the whole icon. Digit font family, style (regular/bold/italic/
    bold italic), digit scale and digit color all come from Settings:
    "icon_font" (FONT_FAMILIES), "icon_font_style", "icon_digit_scale"
    (50-150 % of the auto-fit size), "icon_digit_color" ("auto" =
    contrast-picked, or "#rrggbb") and "icon_text_dx"/"icon_text_dy"
    pixel offsets from center.
    Digit color adapts to the pill when auto (dark on green/orange, white
    on red) and a subtle same-color stroke keeps digits crisp at small
    sizes. high_contrast swaps the pill for black with a white border so
    it stays readable on light/white taskbars. Results are cached.
    """
    if size is None:
        size = ICON_SIZE
    if high_contrast is None:
        high_contrast = bool(SETTINGS.get("high_contrast_icon"))
    font_key = SETTINGS.get("icon_font", "Segoe UI")
    style = SETTINGS.get("icon_font_style", "bold")
    try:
        scale = int(SETTINGS.get("icon_digit_scale", 100))
    except (TypeError, ValueError):
        scale = 100
    digit_color_setting = str(SETTINGS.get("icon_digit_color", "auto"))
    dx = int(SETTINGS.get("icon_text_dx", 0) or 0)
    dy = int(SETTINGS.get("icon_text_dy", 0) or 0)
    key = (text, color, size, high_contrast, font_key, style, scale,
           digit_color_setting, dx, dy)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    margin = max(1, size // 32)
    if high_contrast:
        pill = (0, 0, 0, 255)
        d.rounded_rectangle([margin, margin, size - 1 - margin, size - 1 - margin],
                            radius=max(4, size // 5), fill=pill,
                            outline=(255, 255, 255, 255),
                            width=max(1, size // 24))
    else:
        pill = color
        d.rounded_rectangle([margin, margin, size - 1 - margin, size - 1 - margin],
                            radius=max(4, size // 5), fill=pill)
    font = _icon_font(size, text, font_key, style, scale)
    if _is_hex_color(digit_color_setting):
        h = digit_color_setting.lstrip("#")
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        text_color = (rgb + (255,)) if len(rgb) == 3 else rgb
    else:
        text_color = _pill_text_color(pill)
    stroke = max(1, size // 26)
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # center the INK box (not the layout box) inside the icon
    cx = (size - w) / 2 - bbox[0]
    cy = (size - h) / 2 - bbox[1]
    # clamp the offset so the digits can never leave the pill entirely
    slack_x = max(0.0, half - w / 2) if (half := size / 2 - 2) else 0.0
    slack_y = max(0.0, half - h / 2)
    cx += min(max(dx, -slack_x), slack_x)
    cy += min(max(dy, -slack_y), slack_y)
    d.text((cx, cy), text, font=font, fill=text_color,
           stroke_width=stroke, stroke_fill=text_color)
    _ICON_CACHE[key] = img
    return img


# ---- CSV history ---------------------------------------------------------

def load_history():
    """Load recorded (timestamp, temp) points from the CSV, if any."""
    points = []
    try:
        with open(HISTORY_FILE, newline="") as f:
            for row in csv.reader(f):
                if len(row) != 2:
                    continue
                try:
                    ts, temp = float(row[0]), int(row[1])
                except ValueError:
                    continue
                points.append((ts, temp))
    except OSError:
        pass
    cutoff = time.time() - HISTORY_SECONDS
    return [p for p in points if p[0] >= cutoff]


def save_history(points):
    try:
        with open(HISTORY_FILE, "w", newline="") as f:
            csv.writer(f).writerows(points)
    except OSError:
        pass


# ---- 24-hour minute-resolution history -----------------------------------

def _history24_load():
    """[(ts, temp), ...] from the 24 h store (oldest first)."""
    pts = []
    try:
        with open(HISTORY24_FILE, newline="") as f:
            for row in csv.reader(f):
                if len(row) != 2:
                    continue
                try:
                    pts.append((float(row[0]), int(row[1])))
                except ValueError:
                    continue
    except OSError:
        pass
    return pts


def _history24_save(points):
    try:
        with open(HISTORY24_FILE, "w", newline="") as f:
            csv.writer(f).writerows(points)
    except OSError:
        pass


def _history24_load_state():
    try:
        with open(HISTORY24_STATE, encoding="utf-8") as f:
            data = json.load(f)
        return {"minute": float(data.get("minute", 0.0)),
                "bucket": data.get("bucket")}
    except (OSError, ValueError, TypeError):
        return {"minute": 0.0, "bucket": None}


def _history24_save_state(state):
    try:
        with open(HISTORY24_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def update_history24(points, now, state, temps=None):
    """Add one minute-bucket point to the 24 h history.

    Keeps at most one point per minute (the FIRST second of that
    wall-clock minute), downsampling the 1 Hz in-memory history to a
    stable 24 h x 60 min series. ``temps`` overrides the sample source
    (per-disk temps); when omitted the hottest point of ``points`` wins.
    Returns (points, state) - the trimmed list and the new writer state.
    """
    state = dict(state or {"minute": 0.0, "bucket": None})
    minute = int(now // 60) * 60
    if state.get("minute") == minute:
        return points, state          # already sampled this minute
    if temps:
        samples = [t["temp"] for t in temps if t.get("temp") is not None]
        if not samples:
            return points, state      # no reading -> carry the bucket
        temp = max(samples)
    else:
        minute_pts = [p for p in points if now - 60 <= p[0] <= now]
        if not minute_pts:
            return points, state
        temp = max(t for _, t in minute_pts)
    pts = list(points)
    pts.append((minute, temp))
    cutoff = now - 86400
    while pts and pts[0][0] < cutoff:
        pts.pop(0)
    while len(pts) > HISTORY24_CAPACITY:
        pts.pop(0)
    state = {"minute": float(minute), "bucket": temp}
    return pts, state


def write_history_csv(points, path):
    """Write history points as CSV; returns the path or None on failure."""
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(("timestamp", "temperature_c"))
            for ts, temp in points:
                w.writerow((f"{ts:.0f}", temp))
        return path
    except OSError:
        return None


def export_graph_csv(points, parent=None):
    """Ask for a destination and write the history as CSV. Returns path."""
    from tkinter import filedialog
    name = time.strftime("ssd_temp_history_%Y%m%d_%H%M.csv")
    path = filedialog.asksaveasfilename(
        parent=parent, defaultextension=".csv",
        filetypes=(("CSV", "*.csv"), ("All files", "*.*")),
        initialfile=name)
    if not path:
        return None
    return write_history_csv(points, path)


def export_canvas_png(canvas, parent=None):
    """Save the on-screen graph canvas as PNG (screen capture). Returns path."""
    from tkinter import filedialog
    name = time.strftime("ssd_temp_graph_%Y%m%d_%H%M.png")
    path = filedialog.asksaveasfilename(
        parent=parent, defaultextension=".png",
        filetypes=(("PNG image", "*.png"), ("All files", "*.*")),
        initialfile=name)
    if not path:
        return None
    try:
        from PIL import ImageGrab
        x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
        img = ImageGrab.grab(bbox=(x, y, x + canvas.winfo_width(),
                                   y + canvas.winfo_height()))
        img.save(path)
        return path
    except Exception:
        return None


class App:
    def __init__(self):
        log_event("startup", version=effective_version())
        self.temps = []
        self.history = load_history() if KEEP_HISTORY else []
        self._last_save = 0.0
        self._detail_open = False
        self._graph_open = False
        self._disks_open = False
        self._settings_open = False
        self._about_open = False
        self._graph_win = None
        self._graph_done = threading.Event()
        self._shutdown_requested = False
        self._extra_icons = {}   # key "i:model" -> pystray.Icon (one per extra SSD)
        self._alert_since = None
        self._last_alert = 0.0
        self._last_smart_alert = 0.0
        self._smart_state = load_smart_state()
        self._pending_update = None
        self._nagged_version = None      # nag once per (version, session)
        self._last_update_check = 0.0
        self._last_weekly_report = 0.0   # weekly auto-report timer
        self.history24 = _history24_load()
        self._h24_state = _history24_load_state()
        self.icon = pystray.Icon(
            "ssd_temp",
            icon=make_icon("--", UNKNOWN),
            title=tr("app.title"),
            menu=self._build_menu(),
        )
        self._lock = threading.Lock()

    def _apply_language(self):
        """Retranslate what lives outside windows: tray title + menu."""
        try:
            self.icon.title = tr("app.title")
            self.icon.menu = self._build_menu()
            self.icon.update_menu()
        except Exception:
            pass

    def set_language(self, item):
        """Tray submenu handler: switch UI language and persist it."""
        label = str(item.text)
        lang_by_label = {"English": "en", "ไทย (Thai)": "th",
                         "日本語 (Japanese)": "ja", "中文 (Chinese)": "zh"}
        new_lang = lang_by_label.get(label, "en")
        if SETTINGS.get("language") == new_lang:
            return
        SETTINGS["language"] = new_lang
        save_settings(SETTINGS)
        self._apply_language()

    def _build_menu(self):
        """Assemble the tray menu from translated strings.

        Rebuilt after a language change: pystray menus cannot retranslate
        themselves in place, so we assign a fresh Menu object instead.
        The Language submenu always shows both languages, natively labelled.
        """
        current = SETTINGS.get("language", "en")
        return pystray.Menu(
            pystray.MenuItem(tr("menu.details"), self.show_details, default=True),
            pystray.MenuItem(tr("menu.graph"), self.show_graph),
            pystray.MenuItem(tr("menu.graph24"), self.show_graph24),
            pystray.MenuItem(tr("menu.stats"), self.show_stats),
            pystray.MenuItem(tr("menu.disks"), self.show_disks),
            pystray.MenuItem(tr("menu.diagnostics"), self.copy_diagnostics),
            pystray.MenuItem(tr("menu.refresh"), self.refresh),
            pystray.MenuItem(tr("menu.updates"), self.check_updates_now),
            pystray.MenuItem(tr("menu.selftest"), self.run_update_selftest_ui),
            pystray.MenuItem(tr("menu.restore"), self.restore_previous_version),
            pystray.MenuItem(tr("menu.settings"), self.show_settings),
            pystray.MenuItem(tr("menu.about"), self.show_about),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(tr("menu.history"), self.toggle_history,
                             checked=lambda item: KEEP_HISTORY),
            pystray.MenuItem(
                tr("menu.language"),
                pystray.Menu(
                    pystray.MenuItem("English", self.set_language,
                                     radio=lambda item: current == "en"),
                    pystray.MenuItem("ไทย (Thai)", self.set_language,
                                     radio=lambda item: current == "th"),
                    pystray.MenuItem("日本語 (Japanese)", self.set_language,
                                     radio=lambda item: current == "ja"),
                    pystray.MenuItem("中文 (Chinese)", self.set_language,
                                     radio=lambda item: current == "zh"),
                )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(tr("menu.exit"), self.quit),
        )

    # ---- menu actions ----
    def refresh(self, *_):
        # run off the menu-callback thread so the tray stays responsive
        threading.Thread(target=self.update, daemon=True).start()

    def toggle_history(self, *_):
        global KEEP_HISTORY
        KEEP_HISTORY = not KEEP_HISTORY
        os.environ["SSD_TEMP_RECORD_HISTORY"] = "1" if KEEP_HISTORY else "0"
        SETTINGS["record_history"] = bool(KEEP_HISTORY)
        save_settings(SETTINGS)
        if KEEP_HISTORY and not self.history:
            self.history = load_history()

    def show_settings(self, *_):
        self._spawn_once("_settings_open", self._settings_window)

    def check_updates_now(self, *_):
        threading.Thread(target=self._check_updates, kwargs={"manual": True},
                         daemon=True).start()

    def restore_previous_version(self, *_):
        """Menu: put the pre-update exe back and restart (confirmation first)."""
        backup = _rollback_backup_path()
        app = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None
        if not app or not os.path.isfile(backup):
            ctypes.windll.user32.MessageBoxW(
                None, tr("restore.none"), tr("mb.title"),
                0x40 | 0x40000 | 0x10000)
            return
        rc = ctypes.windll.user32.MessageBoxW(
            None, tr("restore.confirm"), tr("menu.restore"),
            0x24 | 0x40000 | 0x10000)  # MB_ICONQUESTION | MB_YESNO
        if rc != 6:  # IDNO / closed
            return
        log_event("manual_restore", current=effective_version())
        try:
            # stage the CURRENT exe first so the restart is also rollback-safe
            stage_backup_for_rollback(target_version=effective_version())
            shim = build_restore_shim(backup, app)
        except Exception:
            ctypes.windll.user32.MessageBoxW(
                None, tr("restore.failed"), tr("mb.title"), 0x10)
            return
        subprocess.Popen(["cmd", "/c", shim],
                         creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
        self.quit()

    def run_update_selftest_ui(self, *_):
        """Run the update-pipeline self-test and show the result."""
        def work():
            try:
                ok, (checks, failed) = run_update_selftest()
            except Exception:
                ok, checks, failed = False, [], [("self-test crashed", False, "")]
            log_event("selftest", ok=ok, total=len(checks),
                      failed=len(failed))
            total = len(checks)
            if ok:
                body = tr("selftest.pass", n=total)
                icon = 0x40  # MB_ICONINFORMATION
            else:
                names = "\n".join(f"- {name} {detail}".rstrip()
                                  for name, _ok, detail in failed)
                body = tr("selftest.fail", n=len(failed), total=total) \
                    + "\n" + names
                icon = 0x30  # MB_ICONWARNING
            ctypes.windll.user32.MessageBoxW(
                None, body, tr("selftest.title"),
                icon | 0x40000 | 0x10000)  # topmost | set foreground
        threading.Thread(target=work, daemon=True).start()

    def show_about(self, *_):
        self._spawn_once("_about_open", self._about_window)

    def _spawn_once(self, flag_attr, target):
        """Run target() in its own thread, one instance at a time."""
        with self._lock:
            if getattr(self, flag_attr):
                return  # already showing
            setattr(self, flag_attr, True)

        def wrapper():
            try:
                target()
            finally:
                with self._lock:
                    setattr(self, flag_attr, False)

        threading.Thread(target=wrapper, daemon=True).start()

    def show_details(self, *_):
        # a MessageBox inside the menu callback blocks pystray's event loop,
        # making it impossible to close; use a tkinter window in its own thread
        self._spawn_once("_detail_open", self._details_window)

    def show_graph24(self, *_):
        self._spawn_once("_graph24_open", self._graph24_window)

    def show_stats(self, *_):
        self._spawn_once("_stats_open", self._stats_window)

    def _stats_window(self):
        """Standalone 7-day usage summary (same data as the Settings
        Stats tab, no dialog needed). Own thread + tk per project rules."""
        import tkinter as tk
        root = tk.Tk()
        root.title(tr("win.stats"))
        root.attributes("-topmost", True)
        root.resizable(False, False)
        c = ui_palette()
        box = tk.Text(root, width=52, height=13, wrap="word",
                      font=("Segoe UI", 10), relief="solid", bd=1,
                      bg=c["entry_bg"], fg=c["text"])
        box.pack(padx=14, pady=(14, 6))

        def _render():
            lines = format_week_stats(week_stats(load_daily_health()))
            box.config(state="normal")
            box.delete("1.0", "end")
            for i, ln in enumerate(lines):
                tag = ("head",) if ln and i > 1 and not ln.startswith(" ") \
                    else (("muted",) if i == len(lines) - 1 else ())
                box.insert("end", ln + "\n", tag)
            box.tag_configure("head", font=("Segoe UI", 10, "bold"))
            box.tag_configure("muted", font=("Segoe UI", 8),
                              foreground=c["dim"])
            box.config(state="disabled")

        _render()
        tk.Button(root, text=tr("stats.refresh"), font=("Segoe UI", 10),
                  command=_render).pack(side="left",
                                        padx=(14, 4), pady=(0, 12))
        tk.Button(root, text=tr("common.close"), font=("Segoe UI", 10),
                  command=root.destroy).pack(side="left", padx=4,
                                             pady=(0, 12))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Return>", lambda e: root.destroy())
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()
        geo = geometry_of(root)
        if geo:
            save_geometry({"stats": geo})

    def _graph24_window(self):
        """24-hour minute-resolution temperature view (own thread + tk).

        Same frame styling as the fine graph but sourced from the 24 h
        minute store, so it shows a full day even when the 1 Hz history
        is short. Refreshes every 30 s.
        """
        import tkinter as tk
        win = tk.Tk()
        win.title(tr("win.graph24"))
        win.attributes("-topmost", True)
        win.resizable(False, False)
        c = ui_palette()
        W, H, PAD = GRAPH_W, GRAPH_H, GRAPH_PAD
        canvas = tk.Canvas(win, width=W, height=H, bg=c["bg"],
                           highlightthickness=0)
        canvas.pack(padx=12, pady=(12, 4))
        btns = tk.Frame(win)
        btns.pack(pady=(2, 10))

        def _export_png():
            path = export_canvas_png(canvas, parent=win)
            if path:
                self._notify(tr("notify.exported", path=path), tr("app.title"))

        tk.Button(btns, text=tr("graph.export_png"), width=14,
                  command=_export_png,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        tk.Button(btns, text=tr("common.close"), command=win.destroy,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        def _draw():
            cc = ui_palette()
            canvas.delete("all")
            pts = _history24_load()
            now = time.time()
            t0, t1 = now - 86400, now
            span = max(t1 - t0, 60)
            if not pts:
                canvas.create_text(
                    W / 2, H / 2, fill=cc["muted"], font=("Segoe UI", 12),
                    text=tr("graph.no_history"), justify="center")
                return

            def x(t):
                return PAD + (t - t0) / span * (W - 2 * PAD)

            def y(temp):
                return H - PAD - max(0.0, min(1.0, (temp - GRAPH_Y_LO)
                                             / (GRAPH_Y_HI - GRAPH_Y_LO))) \
                    * (H - 2 * PAD)

            # temperature zone bands (green/orange/red), under everything
            for zlo, zhi, zc in temp_zone_bands():
                canvas.create_rectangle(PAD, y(zhi), W - PAD, y(zlo),
                                        fill=zc, outline="", stipple="gray50")

            for gt in range(20, GRAPH_Y_HI, 20):
                yy = y(gt)
                canvas.create_line(PAD, yy, W - PAD, yy, fill=cc["grid"])
                canvas.create_text(PAD - 8, yy, anchor="e", fill=cc["dim"],
                                   font=("Segoe UI", 9), text=f"{gt}°")
            canvas.create_line(PAD, H - PAD, W - PAD, H - PAD,
                               fill=cc["axis"])
            # one hour tick per 3 h
            for hh in range(1, 8):
                xx = PAD + (hh * 3 * 3600) / span * (W - 2 * PAD)
                canvas.create_text(xx, H - PAD + 14, fill=cc["dim"],
                                   font=("Segoe UI", 9),
                                   text=f"-{24 - hh * 3}h")
            coords = []
            for t, temp in pts:
                if t < t0:
                    continue
                coords.extend((x(t), y(temp)))
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=ACCENT, width=2,
                                   joinstyle="round")
            temps = [temp for t, temp in pts if t >= t0]
            if temps:
                lo, hi = min(temps), max(temps)
                avg = sum(temps) / len(temps)
                canvas.create_text(
                    PAD, 12, anchor="nw", font=("Segoe UI", 11, "bold"),
                    fill=cc["text"],
                    text=f"min {lo}°C   max {hi}°C   avg {avg:.1f}°C "
                         f"({len(temps)} min)")

        _draw()

        def _tick():
            with self._lock:
                stop = self._shutdown_requested
            if stop or not win.winfo_exists():
                try:
                    win.destroy()
                except tk.TclError:
                    pass
                return
            try:
                _draw()
            except tk.TclError:
                return
            win.after(30000, _tick)

        win.after(30000, _tick)
        win.mainloop()
        geo = geometry_of(win)
        if geo:
            save_geometry({"graph24": geo})

    def show_graph(self, *_):
        # MUST go through _spawn_once like the other windows: running the tk
        # event loop inside pystray's menu-callback thread blocks the tray
        # (Exit dead) and calling tk methods from a second menu click corrupts
        # the tk interpreter -> the window stops processing events and can
        # never be closed. If already open, raise it via Win32 instead.
        already = self._graph_open
        self._spawn_once("_graph_open", self._graph_window)
        if already:
            self._raise_window(tr("win.graph"))

    def show_disks(self, *_):
        self._spawn_once("_disks_open", self._disks_window)

    def _snapshot(self, attr):
        with self._lock:
            return list(getattr(self, attr))

    @staticmethod
    def _raise_window(title):
        """Bring an existing top-level window to the front (thread-safe).

        Uses plain Win32 so we never touch tkinter objects from outside
        the thread that owns them.
        """
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            SW_RESTORE, SW_SHOW = 9, 5
            if ctypes.windll.user32.IsIconic(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            else:
                ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
            ctypes.windll.user32.SetForegroundWindow(hwnd)

    # ---- windows ----
    def _details_window(self):
        temps = self._snapshot("temps")
        lines = []
        if not temps:
            lines.append((tr("details.no_data"), UNKNOWN))
        else:
            for t in temps:
                if t["temp"] is not None:
                    lines.append((f"{t['model']}:  {t['temp']} °C", temp_color(t["temp"])))
                else:
                    lines.append((f"{t['model']}:  n/a", UNKNOWN))
                for flag in health_flags(t):
                    lines.append((f"  ⚠ {flag}", ORANGE))

        import tkinter as tk
        root = tk.Tk()
        root.title(tr("win.details"))
        root.attributes("-topmost", True)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=24, pady=12)
        frame.pack()
        for text, color in lines:
            tk.Label(frame, text=text, font=("Segoe UI", 13, "bold"),
                     fg=color).pack(anchor="w", pady=2)
        tk.Button(frame, text=tr("common.close"), command=root.destroy,
                  font=("Segoe UI", 10)).pack(pady=(10, 0))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Return>", lambda e: root.destroy())
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()

        geo = geometry_of(root)
        if geo:
            save_geometry({"details": geo})

    def _graph_window(self):
        """Create and own the history graph window (runs in its own thread).

        After mainloop() returns, the thread that created the tk widgets is
        gone; we explicitly NULL out _graph_win so the state stays honest.
        """
        import tkinter as tk
        win = tk.Tk()
        win.title(tr("win.graph"))
        win.attributes("-topmost", True)
        win.resizable(False, False)
        apply_geometry(win, "graph")
        W, H, PAD = GRAPH_W, GRAPH_H, GRAPH_PAD
        c = ui_palette()
        canvas = tk.Canvas(win, width=W, height=H, bg=c["bg"],
                           highlightthickness=0)
        canvas.pack(padx=12, pady=(12, 4))
        btns = tk.Frame(win)
        btns.pack(pady=(2, 10))

        def _export_csv():
            data = self._snapshot("history")
            path = export_graph_csv(data, parent=win)
            if path:
                self._notify(tr("notify.exported", path=path), tr("app.title"))

        def _export_png():
            path = export_canvas_png(canvas, parent=win)
            if path:
                self._notify(tr("notify.exported", path=path), tr("app.title"))

        tk.Button(btns, text=tr("graph.export_csv"), width=14,
                  command=_export_csv,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        tk.Button(btns, text=tr("graph.export_png"), width=14,
                  command=_export_png,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        tk.Button(btns, text=tr("common.close"), command=win.destroy,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

        with self._lock:
            self._graph_win = win

        def _tick():
            # runs on the graph thread only -- all tk access stays here
            with self._lock:
                stop = self._shutdown_requested
            if stop or not win.winfo_exists():
                try:
                    win.destroy()
                except tk.TclError:
                    pass
                return
            try:
                self._draw_graph(canvas, W, H, PAD)
            except tk.TclError:
                return  # window closed mid-draw
            win.after(GRAPH_REFRESH_MS, _tick)

        self._graph_done.clear()
        with self._lock:
            self._shutdown_requested = False
        self._draw_graph(canvas, W, H, PAD)
        win.after(GRAPH_REFRESH_MS, _tick)
        win.mainloop()

        # mainloop returned -> window is gone; finalize on the graph's own
        # thread so the Tcl interpreter is torn down there too
        geo = geometry_of(win)
        if geo:
            save_geometry({"graph": geo})
        with self._lock:
            self._graph_win = None
        self._graph_done.set()

    def _draw_graph(self, canvas, W, H, PAD):
        import tkinter as tk
        Y_LO, Y_HI = GRAPH_Y_LO, GRAPH_Y_HI
        c = ui_palette()
        canvas.delete("all")
        data = self._snapshot("history")
        if not data:
            canvas.create_text(
                W / 2, H / 2, fill=c["muted"], font=("Segoe UI", 12),
                text=tr("graph.no_history"), justify="center")
            return

        # down-sample to at most 500 points for the polyline
        step = max(1, len(data) // 500)
        pts = data[::step]
        if pts[-1] != data[-1]:
            pts.append(data[-1])

        now = time.time()
        t0 = min(pts[0][0], now - HISTORY_SECONDS)
        t1 = now
        span = max(t1 - t0, 60)

        def x(t):
            return PAD + (t - t0) / span * (W - 2 * PAD)

        def y(temp):
            return H - PAD - (temp - Y_LO) / (Y_HI - Y_LO) * (H - 2 * PAD)

        # temperature zone bands (green/orange/red), under everything
        for zlo, zhi, zc in temp_zone_bands(Y_LO, Y_HI):
            canvas.create_rectangle(PAD, y(zhi), W - PAD, y(zlo),
                                    fill=zc, outline="", stipple="gray50")

        # grid + y-axis labels
        for gt in range(20, Y_HI, 20):
            yy = y(gt)
            canvas.create_line(PAD, yy, W - PAD, yy, fill=c["grid"])
            canvas.create_text(PAD - 8, yy, anchor="e", fill=c["dim"],
                               font=("Segoe UI", 9), text=f"{gt}°")
        canvas.create_line(PAD, H - PAD, W - PAD, H - PAD, fill=c["axis"])
        canvas.create_text(W - PAD, H - PAD + 14, anchor="ne", fill=c["dim"],
                           font=("Segoe UI", 9),
                           text=tr("graph.span", minutes=f"{span / 60:.0f}"))

        coords = []
        for t, temp in pts:
            coords.extend((x(t), y(temp)))
        canvas.create_line(*coords, fill=ACCENT, width=2, joinstyle="round")

        temps = [temp for _, temp in pts]
        lo, hi = min(temps), max(temps)
        avg = sum(temps) / len(temps)
        canvas.create_text(
            PAD, 12, anchor="nw", font=("Segoe UI", 11, "bold"), fill=c["text"],
            text=f"min {lo}°C   max {hi}°C   avg {avg:.1f}°C   ({len(pts)} samples)")

    def _disks_window(self):
        import tkinter as tk
        disks = list_all_disks()
        root = tk.Tk()
        root.title(tr("win.disks"))
        root.attributes("-topmost", True)
        root.resizable(False, False)
        apply_geometry(root, "disks")
        frame = tk.Frame(root, padx=24, pady=12)
        frame.pack()
        tk.Label(frame, text=tr("disks.legend"),
                 font=("Segoe UI", 9), fg="#64748b").pack(anchor="w")
        if not disks:
            tk.Label(frame, text=tr("disks.none"),
                     font=("Segoe UI", 12, "bold"), fg=UNKNOWN).pack(anchor="w", pady=6)
        for d in disks:
            included = is_internal_ssd(d)
            mark = "✓" if included else "✗"
            color = GREEN if included else UNKNOWN
            temp = f"{d['temp']} °C" if d["temp"] is not None else "n/a"
            tk.Label(frame,
                     text=f"{mark}  {d['model']}   [{d['media']} / {d['bus']}]   {temp}",
                     font=("Segoe UI", 11, "bold"), fg=color,
                     anchor="w").pack(anchor="w", pady=2)
        tk.Button(frame, text=tr("common.close"), command=root.destroy,
                  font=("Segoe UI", 10)).pack(pady=(10, 0))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Return>", lambda e: root.destroy())
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()
        geo = geometry_of(root)
        if geo:
            save_geometry({"disks": geo})

    def _settings_window(self):
        """Settings dialog with tabs (General / Icon / Updates / Health).

        The Icon tab has a live preview (two sizes) that re-renders as
        values change; everything applies immediately on save (icons
        re-render on the next poll, menu rebuilds on language change).
        """
        import tkinter as tk
        from tkinter import ttk, messagebox
        import base64
        import io

        with self._lock:
            current = dict(SETTINGS)

        root = tk.Tk()
        root.title(tr("win.settings"))
        root.attributes("-topmost", True)
        root.resizable(False, False)
        outer = tk.Frame(root, padx=18, pady=12)
        outer.pack()

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        tab_general = tk.Frame(notebook, padx=14, pady=8)
        tab_icon = tk.Frame(notebook, padx=14, pady=8)
        tab_updates = tk.Frame(notebook, padx=14, pady=8)
        tab_health = tk.Frame(notebook, padx=14, pady=8)
        tab_stats = tk.Frame(notebook, padx=14, pady=8)
        notebook.add(tab_general, text=tr("tab.general"))
        notebook.add(tab_icon, text=tr("tab.icon"))
        notebook.add(tab_updates, text=tr("tab.updates"))
        notebook.add(tab_health, text=tr("tab.health"))
        notebook.add(tab_stats, text=tr("tab.stats"))

        def add_spin(tab, label, key, lo, hi, row):
            tk.Label(tab, text=label, font=("Segoe UI", 10),
                     anchor="w").grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(current[key]))
            ttk.Spinbox(tab, from_=lo, to=hi, width=8,
                        textvariable=var).grid(
                row=row, column=1, padx=(14, 0), pady=3)
            vars_[key] = var

        vars_ = {}

        # ---- General tab: polling, alerts, history, language ----------
        add_spin(tab_general, tr("settings.poll"), "poll_seconds", 1, 60, 0)
        add_spin(tab_general, tr("settings.threshold"),
                 "alert_threshold", 40, 90, 1)
        add_spin(tab_general, tr("settings.sustain"),
                 "alert_sustain_seconds", 0, 600, 2)
        add_spin(tab_general, tr("settings.cooldown"),
                 "alert_cooldown_minutes", 1, 120, 3)
        add_spin(tab_general, tr("settings.history"),
                 "history_minutes", 5, 240, 4)
        record_var = tk.BooleanVar(value=current["record_history"])
        ttk.Checkbutton(tab_general, text=tr("settings.record"),
                        variable=record_var).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        compact_var = tk.BooleanVar(value=current.get("compact_tooltip", True))
        ttk.Checkbutton(tab_general, text=tr("settings.compact_tooltip"),
                        variable=compact_var).grid(
            row=5, column=1, sticky="w", padx=(14, 0), pady=(8, 0))
        tk.Label(tab_general, text=tr("settings.language"),
                 font=("Segoe UI", 10), anchor="w").grid(
            row=6, column=0, sticky="w", pady=3)
        lang_var = tk.StringVar(value=current.get("language", "en"))
        ttk.Combobox(tab_general, textvariable=lang_var, width=14,
                     values=UI_LANGUAGES, state="readonly").grid(
            row=6, column=1, padx=(14, 0), pady=3)

        autostart_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(tab_general, text=tr("settings.autostart"),
                        variable=autostart_var).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        smart_var = tk.BooleanVar(value=current.get("smart_alerts", True))
        ttk.Checkbutton(tab_general, text=tr("settings.smart_alerts"),
                        variable=smart_var).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # UI theme (dark/light) for windows and reports
        tk.Label(tab_general, text=tr("settings.ui_theme"),
                 font=("Segoe UI", 10), anchor="w").grid(
            row=11, column=0, sticky="w", pady=(10, 0))
        uitheme_var = tk.StringVar(value=current.get("ui_theme", "auto"))
        ttk.Combobox(tab_general, textvariable=uitheme_var, width=14,
                     values=("auto", "dark", "light"),
                     state="readonly").grid(
            row=11, column=1, padx=(14, 0), pady=(10, 0))

        # weekly auto-report: checkbox + folder chooser
        weekly_var = tk.BooleanVar(value=current.get("weekly_report_enabled",
                                                     False))
        ttk.Checkbutton(tab_general, text=tr("settings.weekly_report"),
                        variable=weekly_var).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))
        weekly_dir_var = tk.StringVar(value=current.get("weekly_report_dir",
                                                        ""))
        tk.Label(tab_general, text=tr("settings.weekly_dir"),
                 font=("Segoe UI", 9), fg="#64748b", anchor="w").grid(
            row=10, column=0, sticky="w")

        def _choose_weekly_dir():
            from tkinter import filedialog
            d = filedialog.askdirectory(parent=root,
                                        title=tr("settings.weekly_dir"))
            if d:
                weekly_dir_var.set(d)

        tk.Button(tab_general, text=tr("settings.weekly_browse"),
                  font=("Segoe UI", 9), command=_choose_weekly_dir).grid(
            row=10, column=1, sticky="w", padx=(14, 0))

        # ---- Icon tab: theme, size, font, color, offsets, preview -----
        tk.Label(tab_icon, text=tr("settings.theme"), font=("Segoe UI", 10),
                 anchor="w").grid(row=0, column=0, sticky="w", pady=3)
        theme_var = tk.StringVar(value=current.get("icon_theme", "classic"))
        theme_box = ttk.Combobox(tab_icon, textvariable=theme_var, width=12,
                                 values=tuple(THEMES), state="readonly")
        theme_box.grid(row=0, column=1, padx=(14, 0), pady=3)

        add_spin(tab_icon, tr("settings.icon_size"), "icon_size", 16, 128, 1)

        tk.Label(tab_icon, text=tr("settings.font"), font=("Segoe UI", 10),
                 anchor="w").grid(row=2, column=0, sticky="w", pady=3)
        font_var = tk.StringVar(value=current["icon_font"])
        ttk.Combobox(tab_icon, textvariable=font_var, width=14,
                     values=tuple(FONT_FAMILIES), state="readonly").grid(
            row=2, column=1, padx=(14, 0), pady=3)

        tk.Label(tab_icon, text=tr("settings.font_style"),
                 font=("Segoe UI", 10), anchor="w").grid(
            row=3, column=0, sticky="w", pady=3)
        style_var = tk.StringVar(value=current["icon_font_style"])
        ttk.Combobox(tab_icon, textvariable=style_var, width=14,
                     values=FONT_STYLES, state="readonly").grid(
            row=3, column=1, padx=(14, 0), pady=3)

        add_spin(tab_icon, tr("settings.digit_scale"),
                 "icon_digit_scale", 50, 150, 4)

        tk.Label(tab_icon, text=tr("settings.digit_color"),
                 font=("Segoe UI", 10), anchor="w").grid(
            row=5, column=0, sticky="w", pady=3)
        color_var = tk.StringVar(value=current["icon_digit_color"])
        ttk.Combobox(tab_icon, textvariable=color_var, width=14,
                     values=COLOR_PRESETS).grid(
            row=5, column=1, padx=(14, 0), pady=3)

        add_spin(tab_icon, tr("settings.dx"), "icon_text_dx", -50, 50, 6)
        add_spin(tab_icon, tr("settings.dy"), "icon_text_dy", -50, 50, 7)

        hc_var = tk.BooleanVar(value=current["high_contrast_icon"])
        ttk.Checkbutton(tab_icon, text=tr("settings.high_contrast"),
                        variable=hc_var).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

        def apply_theme(*_):
            """Theme preset: copy its font/color/contrast into the dialog.

            Selecting "custom" changes nothing - it just marks that the
            hand-tuned values below are what should be saved.
            """
            preset = THEMES.get(theme_var.get())
            if not preset:
                return
            if "icon_font" in preset:
                font_var.set(preset["icon_font"])
            if "icon_font_style" in preset:
                style_var.set(preset["icon_font_style"])
            if "icon_digit_color" in preset:
                color_var.set(preset["icon_digit_color"])
            if "high_contrast_icon" in preset:
                hc_var.set(preset["high_contrast_icon"])
            if "icon_text_dx" in preset:
                vars_["icon_text_dx"].set(str(preset["icon_text_dx"]))
            if "icon_text_dy" in preset:
                vars_["icon_text_dy"].set(str(preset["icon_text_dy"]))

        theme_box.bind("<<ComboboxSelected>>", apply_theme)

        # live preview, two sizes: the actual tray size + a large 96 px one
        preview_lbls = []
        try:
            preview_row = tk.Frame(tab_icon)
            preview_row.grid(row=9, column=0, columnspan=2,
                             sticky="w", pady=(12, 0))
            tk.Label(preview_row, text=tr("settings.preview"),
                     font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
            for _ in range(2):
                lbl = tk.Label(preview_row, bg=ui_palette()["bg"], bd=1,
                               relief="solid", padx=4)
                lbl.pack(side="left", padx=(0, 10))
                preview_lbls.append(lbl)
        except Exception:
            preview_lbls = []

        def render_preview(*_):
            """Re-render the preview icons from the dialog values.

            Overrides SETTINGS only for the render, then restores them -
            the real settings stay untouched until Save. Never raises:
            a preview failure must not break the Settings window.
            """
            if not preview_lbls:
                return
            try:
                trial = dict(current)
                for key, var in vars_.items():
                    trial[key] = int(var.get())
                trial["icon_font"] = font_var.get()
                trial["icon_font_style"] = style_var.get()
                trial["icon_digit_color"] = color_var.get().strip() or "auto"
                trial["high_contrast_icon"] = bool(hc_var.get())
                trial["language"] = lang_var.get()
                trial = _validate_settings(trial)
            except (ValueError, tk.TclError):
                return  # half-typed number: keep the previous preview
            keys = ("icon_size", "icon_font", "icon_font_style",
                    "icon_digit_scale", "icon_digit_color",
                    "icon_text_dx", "icon_text_dy", "high_contrast_icon")
            saved = {k: SETTINGS.get(k) for k in keys}
            try:
                for k in keys:
                    SETTINGS[k] = trial[k]
                imgs = [make_icon("42", GREEN, size=trial["icon_size"]),
                        make_icon("42", GREEN, size=96)]
            finally:
                for k, v in saved.items():
                    SETTINGS[k] = v
            for lbl, img in zip(preview_lbls, imgs):
                try:
                    with io.BytesIO() as buf:
                        img.save(buf, "PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    # keep a real reference to the PhotoImage: assigning the
                    # label's own (string) image option back would leave the
                    # new PhotoImage unreferenced, so GC destroys the Tk
                    # image and the settings window fails to draw (the
                    # v1.11.0 bug)
                    photo = tk.PhotoImage(data=b64, master=lbl)
                    lbl.config(image=photo)
                    lbl.image = photo
                except tk.TclError:
                    pass

        for var in (list(vars_.values()) + [font_var, color_var,
                                            hc_var, lang_var, compact_var]):
            try:
                var.trace_add("write", render_preview)
            except Exception:
                pass
        try:
            render_preview()
        except Exception:
            pass  # a broken preview must never stop Settings from opening

        # ---- Updates tab: channel, check interval ---------------------
        tk.Label(tab_updates, text=tr("settings.channel"),
                 font=("Segoe UI", 10), anchor="w").grid(
            row=0, column=0, sticky="w", pady=3)
        channel_var = tk.StringVar(value=current.get("update_channel", "stable"))
        ttk.Combobox(tab_updates, textvariable=channel_var, width=14,
                     values=("stable", "pre-release"), state="readonly").grid(
            row=0, column=1, padx=(14, 0), pady=3)
        add_spin(tab_updates, tr("settings.check_interval"),
                 "update_check_interval_minutes", 5, 1440, 1)

        # ---- Health tab: per-disk SMART summary (live probe on refresh) --
        status_lbl = tk.Label(tab_health, font=("Segoe UI", 10, "bold"),
                              anchor="w", text="...")
        status_lbl.grid(row=0, column=0, sticky="w")

        # ---- Stats tab: 7-day usage summary ---------------------------
        stats_box = tk.Text(tab_stats, width=46, height=12, wrap="word",
                            font=("Segoe UI", 10), state="disabled",
                            relief="solid", bd=1, bg=ui_palette()["entry_bg"])
        stats_box.grid(row=0, column=0, columnspan=2, sticky="we",
                       pady=(0, 8))

        def _render_stats():
            lines = format_week_stats(week_stats(load_daily_health()))
            stats_box.config(state="normal")
            stats_box.delete("1.0", "end")
            for i, ln in enumerate(lines):
                tag = ("head",) if ln and i > 1 and not ln.startswith(" ") \
                    else (("muted",) if i == len(lines) - 1 else ())
                stats_box.insert("end", ln + "\n", tag)
            stats_box.tag_configure("head",
                                    font=("Segoe UI", 10, "bold"))
            stats_box.tag_configure("muted",
                                    font=("Segoe UI", 8),
                                    foreground="#64748b")
            stats_box.config(state="disabled")

        _render_stats()
        stats_refresh = tk.Button(tab_stats, text=tr("stats.refresh"),
                                  font=("Segoe UI", 10),
                                  command=_render_stats)
        stats_refresh.grid(row=1, column=0, sticky="w")
        refresh_btn = tk.Button(tab_health, text=tr("health.refresh"),
                                font=("Segoe UI", 10), width=10)
        refresh_btn.grid(row=0, column=1, sticky="e", padx=(14, 0))
        trend_btn = tk.Button(tab_health, text=tr("health.trend"),
                              font=("Segoe UI", 10), width=12)
        trend_btn.grid(row=0, column=2, sticky="e", padx=(14, 0))
        health_box = tk.Text(tab_health, width=46, height=12, wrap="word",
                             font=("Segoe UI", 10), state="disabled",
                             relief="solid", bd=1, bg="#f8fafc")
        health_box.grid(row=1, column=0, columnspan=3, sticky="we",
                        pady=(6, 0))
        for _tag, _color in (("ok", GREEN), ("warn", ORANGE),
                             ("bad", RED), ("muted", UNKNOWN),
                             ("head", "#0f172a")):
            health_box.tag_configure(_tag, foreground=_color)

        def _fill_health(temps):
            """Render the probe results into the read-only text box."""
            health_box.config(state="normal")
            health_box.delete("1.0", "end")
            any_flags = False
            for t in temps:
                health_box.insert("end", str(t["model"]) + "\n", ("head",))
                temp = t["temp"]
                if temp is not None:
                    ttag = "ok" if temp < 51 else "warn" if temp < 65 else "bad"
                    health_box.insert(
                        "end", f"  {tr('health.temp')}: {temp} °C\n", (ttag,))
                else:
                    health_box.insert(
                        "end", f"  {tr('health.temp')}: n/a\n", ("muted",))
                wear = t.get("wear")
                wtxt = f"{wear}%" if wear is not None else "n/a"
                health_box.insert("end", f"  {tr('health.wear')}: {wtxt}\n")
                rerr, uerr = t.get("read_errors"), t.get("unfixed_errors")
                health_box.insert(
                    "end",
                    f"  {tr('health.read_errors')}: "
                    f"{rerr if rerr is not None else 'n/a'}"
                    f"  ({tr('health.unfixed')}: "
                    f"{uerr if uerr is not None else 'n/a'})\n",
                    ("muted",))
                for flag in health_flags(t):
                    any_flags = True
                    health_box.insert("end", f"  ⚠ {flag}\n", ("warn",))
                health_box.insert("end", "\n")
            health_box.config(state="disabled")
            if not temps:
                status_lbl.config(text=tr("health.no_data"), fg=RED)
            elif any_flags:
                status_lbl.config(text=tr("health.status.warn"), fg=ORANGE)
            else:
                status_lbl.config(text=tr("health.status.ok"), fg=GREEN)

        def _refresh_health():
            """Probe SMART in a worker thread, then fill the box on the tk
            thread (subprocess can take a couple of seconds)."""
            refresh_btn.config(state="disabled")
            status_lbl.config(text="...", fg=UNKNOWN)

            def work():
                try:
                    found = read_temps()
                except Exception:
                    found = []

                def done():
                    try:
                        _fill_health(found)
                    except tk.TclError:
                        return  # window closed while probing
                    refresh_btn.config(state="normal")

                root.after(0, done)

            threading.Thread(target=work, daemon=True).start()

        refresh_btn.config(command=_refresh_health)
        root.after(300, _refresh_health)

        def _show_trend():
            """Daily health aggregates (health_daily.csv) in a child window."""
            rows = load_daily_health()
            if not rows:
                messagebox.showinfo(tr("health.trend"),
                                    tr("health.trend_empty"), parent=root)
                return
            models = []
            for r in rows:
                if r.get("model") and r["model"] not in models:
                    models.append(r["model"])
            win = tk.Toplevel(root)
            win.title(tr("health.trend"))
            win.attributes("-topmost", True)
            box = tk.Text(win, width=58, height=18, wrap="none",
                          font=("Consolas", 10), relief="solid", bd=1,
                          bg="#f8fafc")
            box.pack(padx=10, pady=10)
            for model in models:
                box.insert("end", model + "\n", ("head",))
                box.insert("end",
                           "  date         avg    min    max   wear\n",
                           ("muted",))
                for date, avg, lo, hi, wear in trend_series(rows, model):
                    wear_txt = f"{wear}%" if wear is not None else "-"
                    box.insert(
                        "end",
                        f"  {date}  {avg:5.1f}  {lo:4.0f}  {hi:4.0f}"
                        f"  {wear_txt}\n")
                box.insert("end", "\n")
            for _tag, _color in (("head", "#0f172a"), ("muted", "#64748b")):
                box.tag_configure(_tag, foreground=_color)
            box.config(state="disabled")
            tk.Button(win, text=tr("common.close"), command=win.destroy,
                      font=("Segoe UI", 10)).pack(pady=(0, 10))
            win.bind("<Escape>", lambda e: win.destroy())

        trend_btn.config(command=_show_trend)

        report_btn = tk.Button(tab_health, text=tr("health.report"),
                               font=("Segoe UI", 10), width=14)
        report_btn.grid(row=0, column=3, sticky="e", padx=(14, 0))

        def _open_report():
            report_btn.config(state="disabled")
            try:
                path = open_weekly_report(parent=root)
                if not path:
                    messagebox.showinfo(tr("health.report"),
                                        tr("health.trend_empty"), parent=root)
            finally:
                report_btn.config(state="normal")

        report_btn.config(command=_open_report)

        export_btn = tk.Button(tab_health, text=tr("health.export"),
                               font=("Segoe UI", 10), width=12)
        export_btn.grid(row=0, column=4, sticky="e", padx=(14, 0))

        def _export_report():
            export_btn.config(state="disabled")
            try:
                path = save_weekly_report_as(parent=root)
                if path:
                    self._notify(tr("notify.exported", path=path),
                                 tr("app.title"))
            finally:
                export_btn.config(state="normal")

        export_btn.config(command=_export_report)

        csv_btn = tk.Button(tab_health, text=tr("health.csv"),
                            font=("Segoe UI", 10), width=12)
        csv_btn.grid(row=0, column=5, sticky="e", padx=(14, 0))

        def _export_csv():
            csv_btn.config(state="disabled")
            try:
                from tkinter import filedialog
                name = time.strftime("ssd_health_daily_%Y%m%d.csv")
                path = filedialog.asksaveasfilename(
                    parent=root, defaultextension=".csv",
                    filetypes=(("CSV", "*.csv"), ("All files", "*.*")),
                    initialfile=name)
                if path:
                    out = write_health_csv(path)
                    if out:
                        self._notify(tr("notify.exported", path=out),
                                     tr("app.title"))
                    else:
                        messagebox.showinfo(tr("health.trend"),
                                            tr("health.trend_empty"),
                                            parent=root)
            finally:
                csv_btn.config(state="normal")

        csv_btn.config(command=_export_csv)

        note = tk.Label(outer, text=tr("settings.note"),
                        font=("Segoe UI", 8), fg="#64748b")
        note.pack(anchor="w", pady=(6, 0))

        apply_geometry(root, "settings")

        def collect_validated():
            """Dialog values -> validated settings dict (None on error)."""
            color = color_var.get().strip()
            if color != "auto" and not _is_hex_color(color):
                messagebox.showerror(tr("mb.title"), tr("settings.bad_color"),
                                     parent=root)
                return None
            try:
                vals = dict(current)
                for key, var in vars_.items():
                    vals[key] = int(var.get())
                vals["record_history"] = bool(record_var.get())
                vals["smart_alerts"] = bool(smart_var.get())
                vals["compact_tooltip"] = bool(compact_var.get())
                vals["weekly_report_enabled"] = bool(weekly_var.get())
                vals["weekly_report_dir"] = weekly_dir_var.get().strip()
                vals["ui_theme"] = uitheme_var.get()
                vals["high_contrast_icon"] = bool(hc_var.get())
                vals["icon_font"] = font_var.get()
                vals["icon_font_style"] = style_var.get()
                vals["icon_digit_color"] = color
                vals["icon_theme"] = theme_var.get()
                vals["update_channel"] = channel_var.get()
                vals["language"] = lang_var.get()
            except ValueError:
                messagebox.showerror(tr("mb.title"), tr("settings.int_error"),
                                     parent=root)
                return None
            return _validate_settings(vals)

        def on_apply():
            """Save + live-apply WITHOUT closing the window. True on success."""
            global POLL_SECONDS, KEEP_HISTORY
            validated = collect_validated()
            if validated is None:
                return False
            with self._lock:
                SETTINGS.clear()
                SETTINGS.update(validated)
            POLL_SECONDS = SETTINGS["poll_seconds"]
            if os.environ.get("SSD_TEMP_RECORD_HISTORY") is None:
                KEEP_HISTORY = SETTINGS["record_history"]
            if not save_settings(validated):
                messagebox.showerror(
                    tr("mb.title"),
                    tr("settings.save_error", path=CONFIG_FILE), parent=root)
                return False
            # apply what cannot wait for the next poll: tray title and the
            # menu (pystray menus must be replaced wholesale on retranslate)
            self._apply_language()
            set_autostart(bool(autostart_var.get()))
            return True

        def on_save():
            if on_apply():
                root.destroy()

        btns = tk.Frame(outer)
        btns.pack(pady=(10, 0))
        tk.Button(btns, text=tr("settings.save"), width=10, command=on_save,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        tk.Button(btns, text=tr("settings.apply"), width=10, command=on_apply,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        tk.Button(btns, text=tr("settings.cancel"), width=10,
                  command=root.destroy,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()

        geo = geometry_of(root)
        if geo:
            save_geometry({"settings": geo})

    # ---- auto-update ----
    def _check_updates(self, manual=False):
        """Look for a newer GitHub release; notify / offer to install.

        Runs on a worker thread; never raises. With no update available the
        user only sees a message when the check was manual.
        """
        repo = SETTINGS.get("github_repo") or DEFAULT_SETTINGS["github_repo"]
        prerelease = bool(SETTINGS.get("update_channel") == "pre-release")
        release = fetch_latest_release(repo, include_prereleases=prerelease)
        if not release:
            if manual:
                self._notify(tr("notify.no_update"),
                             tr("notify.title.update"))
            return
        url, version = select_release_asset(release, prefer_prerelease=prerelease)
        if not url or not is_newer_version(version):
            if manual:
                self._notify(
                    tr("notify.latest", local=effective_version()),
                    tr("notify.title.update"))
            return
        if version_is_broken(version):
            log_event("update_skipped_broken", remote=version)
            if manual:
                self._notify(
                    tr("notify.broken_skipped", version=version),
                    tr("notify.title.update"))
            return
        with self._lock:
            already = self._nagged_version == version
            self._pending_update = (url, version)
            self._nagged_version = version
        if not already:
            log_event("update_available", remote=version,
                      local=effective_version())
            # nag once per release per session; the Update menu item and the
            # About window stay available the whole time
            self._notify(
                tr("notify.available", remote=version,
                   local=effective_version()),
                title=tr("notify.title.available"))

    def _install_update(self, *_):
        """Download the new setup exe, verify its SHA-256, then update.

        Runs the installer through a cmd shim that first waits for this app
        to exit (setup.iss AppMutex would otherwise abort the silent
        install). Exit codes of the shim: 0 = installed, 1 = installer
        failed/aborted, 1002/1004 = pre-install stage (msi-style,
        inconclusive), None = the wait timed out.
        """
        with self._lock:
            pending = self._pending_update
        if not pending:
            return
        url, version = pending
        self._notify(tr("notify.downloading", version=version),
                     tr("app.title"))

        def worker():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ssd-temp-monitor"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                # verify against SHA256SUMS.txt published with the release
                sums_url = url.rsplit("/", 1)[0] + "/SHA256SUMS.txt"
                req = urllib.request.Request(sums_url, headers={"User-Agent": "ssd-temp-monitor"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    sums_text = resp.read().decode("utf-8", "replace")
                filename = url.rsplit("/", 1)[1]
                if not verify_asset(data, sums_text, filename):
                    self._notify(tr("notify.bad_checksum"),
                                 tr("notify.title.update"))
                    return
                dest = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                                    f"ssd_temp_monitor_setup_{version}.exe")
                with open(dest, "wb") as f:
                    f.write(data)
                stage_backup_for_rollback(target_version=version)
                shim = build_update_shim(dest, restart_path=_own_restart_path())
            except Exception:
                self._notify(tr("notify.download_failed"),
                             tr("notify.title.update"))
                return
            self._notify(tr("notify.installing", version=version),
                         tr("app.title"))
            log_event("update_install_start", remote=version)
            # exit so the installer can replace the exe; the shim waits for
            # us to let go of the AppMutex before starting the installer
            self.quit()
            code = wait_and_install(shim)
            log_event("update_install_result", code=code)
            if code in (0, 1002, 1004):
                return  # installer succeeded (or handed off to a restart)
            try:
                os.remove(shim)
            except OSError:
                pass
            ctypes.windll.user32.MessageBoxW(
                None,
                tr("notify.install_failed", version=version, code=code),
                tr("notify.title.update"), 0x10 | 0x40000 | 0x10000)

        threading.Thread(target=worker, daemon=True).start()

    def _about_window(self):
        """Small About dialog: version, repo link, update check button."""
        import tkinter as tk
        import webbrowser

        root = tk.Tk()
        root.title(tr("win.about"))
        root.attributes("-topmost", True)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=26, pady=14)
        frame.pack()
        tk.Label(frame, text=tr("app.title"),
                 font=("Segoe UI", 15, "bold"),
                 fg="#e2e8f0").pack(anchor="w")
        tk.Label(frame, text=f"Version {effective_version()}",
                 font=("Segoe UI", 10),
                 fg="#94a3b8").pack(anchor="w", pady=(2, 6))
        repo = SETTINGS.get("github_repo") or DEFAULT_SETTINGS["github_repo"]
        link = tk.Label(frame, text=f"github.com/{repo}",
                        font=("Segoe UI", 10, "underline"), fg="#38bdf8",
                        cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>",
                  lambda e: webbrowser.open(f"https://github.com/{repo}/releases/latest"))

        # live "latest release" line: fetched on a worker thread, applied on
        # the tk thread via root.after (no cross-thread tk mutation)
        latest_lbl = tk.Label(frame, text=tr("about.latest.checking"),
                              font=("Segoe UI", 10), fg="#94a3b8")
        latest_lbl.pack(anchor="w", pady=(2, 0))
        pre = bool(SETTINGS.get("update_channel") == "pre-release")

        def apply_latest(release):
            try:
                if not release:
                    latest_lbl.config(text=tr("about.latest.unknown"))
                    return
                tag = str(release.get("tag_name") or "?")
                if not pre and _is_prerelease(release):
                    latest_lbl.config(
                        text=tr("about.latest.stable_none", tag=tag))
                    return
                if is_newer_version(tag):
                    latest_lbl.config(
                        text=tr("about.latest.newer", tag=tag),
                        fg="#fbbf24")
                else:
                    latest_lbl.config(
                        text=tr("about.latest.uptodate", tag=tag),
                        fg="#86efac")
            except Exception:
                pass

        def fetch_latest():
            rel = fetch_latest_release(repo, include_prereleases=pre)
            try:
                root.after(0, lambda: apply_latest(rel))
            except Exception:
                pass  # window already closed

        threading.Thread(target=fetch_latest, daemon=True).start()

        btns = tk.Frame(frame)
        btns.pack(pady=(12, 0))
        tk.Button(btns, text=tr("about.check_updates"), width=16,
                  command=lambda: self.check_updates_now(),
                  font=("Segoe UI", 9)).pack(side="left", padx=4)
        tk.Button(btns, text=tr("about.close"), width=10, command=root.destroy,
                  font=("Segoe UI", 9)).pack(side="left", padx=4)
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()

    def _notify(self, message, title):
        """Toast that opens the app on click; falls back to the tray balloon."""
        try:
            if not notify_actionable(message, title):
                self.icon.notify(message, title=title)
        except Exception:
            try:
                self.icon.notify(message, title=title)
            except Exception:
                pass

    # ---- per-disk icons (multi-SSD support) ----
    def _sync_extra_icons(self, temps):
        """Show one extra tray icon per additional internal SSD (2nd onward).

        The primary icon always shows the hottest SSD; with more than one
        internal SSD every further disk gets its own icon + tooltip. Icons
        are added/removed dynamically as disks appear or disappear.
        """
        if SETTINGS.get("multi_disk_icons"):
            desired = {f"{i}:{t['model']}": (t["model"], t["temp"])
                       for i, t in enumerate(temps) if i > 0}
        else:
            desired = {}
        with self._lock:
            current = dict(self._extra_icons)
        # remove icons whose disk disappeared
        for key, icon in current.items():
            if key not in desired:
                with self._lock:
                    self._extra_icons.pop(key, None)
                try:
                    icon.stop()
                except Exception:
                    pass
        # add new icons / update existing ones
        for key, (model, temp) in desired.items():
            icon = current.get(key)
            if icon is None:
                icon = self._make_extra_icon(key, model, temp)
                with self._lock:
                    self._extra_icons[key] = icon
                threading.Thread(target=icon.run, daemon=True).start()
            else:
                self._update_extra_icon(icon, model, temp)

    def _make_extra_icon(self, key, model, temp):
        import pystray
        icon = pystray.Icon(
            f"ssd_temp_extra_{abs(hash(key)) % 100000}",
            icon=make_icon(str(temp) if temp is not None else "--",
                           temp_color(temp)),
            title=f"{model}: {temp}C" if temp is not None else f"{model}: n/a",
            menu=pystray.Menu(
                pystray.MenuItem(tr("menu.details"), self.show_details, default=True),
                pystray.MenuItem(tr("menu.exit"), self.quit),
            ),
        )
        return icon

    @staticmethod
    def _update_extra_icon(icon, model, temp):
        icon.icon = make_icon(str(temp) if temp is not None else "--",
                              temp_color(temp))
        icon.title = f"{model}: {temp}C" if temp is not None else f"{model}: n/a"

    def copy_diagnostics(self, *_):
        """Copy a troubleshooting snapshot to the clipboard (menu thread OK:
        only win32 clipboard calls, no tk)."""
        with self._lock:
            temps = list(self.temps)
        lines = [
            f"SSD Temperature Monitor diagnostics",
            f"version: {effective_version()} (build {APP_VERSION})",
            f"admin: {is_admin()}",
            f"settings: {json.dumps(SETTINGS, ensure_ascii=False)}",
            "disks:",
        ]
        if temps:
            for t in temps:
                lines.append(
                    f"  {t['model']}: {t['temp']}C"
                    if t["temp"] is not None else f"  {t['model']}: n/a")
        else:
            lines.append("  (no data yet)")
        text = "\n".join(lines)
        try:
            import ctypes.wintypes
            k32 = ctypes.WinDLL("user32", use_last_error=True)
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            k32.OpenClipboard(0)
            try:
                k32.EmptyClipboard()
                buf = ctypes.create_unicode_buffer(text)
                size = (len(buf) + 1) * ctypes.sizeof(ctypes.c_wchar)
                h = k32.GlobalAlloc(GMEM_MOVEABLE, size)
                p = k32.GlobalLock(h)
                ctypes.memmove(p, buf, size)
                k32.GlobalUnlock(h)
                k32.SetClipboardData(CF_UNICODETEXT, h)
            finally:
                k32.CloseClipboard()
            self._notify(tr("notify.copied"), tr("app.title"))
        except Exception:
            pass

    def _smart_watch(self, temps):
        """Proactive SMART watchdog: fire once when something NEW degrades.

        Two triggers per disk: uncorrected read errors increasing beyond
        the last persisted snapshot, and wear newly crossing into a worse
        band (75%/90%). State survives restarts so already-known damage
        never re-alerts; the snapshot is written only when it changes.
        """
        if not SETTINGS.get("smart_alerts", True):
            return
        now = time.time()
        if (now - self._last_smart_alert
                < SMART_ALERT_COOLDOWN_MINUTES * 60):
            return
        state, fired = smart_watch_changes(temps, self._smart_state)
        try:
            slope_msg, state = wear_slope_alert(
                load_daily_health(), now=now, state=state)
        except Exception:
            slope_msg = None
        if slope_msg:
            fired.append(slope_msg)
        if state != self._smart_state:
            self._smart_state = state
            save_smart_state(state)   # rare: only when a counter changed
        if not fired:
            return
        self._last_smart_alert = now
        log_event("smart_alert", items=len(fired))
        body = "\n".join(fired)
        threading.Thread(
            target=lambda: self._notify(body, title=tr("smart.title")),
            daemon=True).start()

    def _alert_drive(self, hottest, now):
        """Advance the alert state machine; return True if an alert fired."""
        with self._lock:
            fire, since = alert_state(hottest, self._alert_since, self._last_alert, now)
            self._alert_since = since
            if fire:
                self._last_alert = now
        if fire:
            threading.Thread(target=self._alert, daemon=True).start()
        return fire

    def _alert(self):
        """Show a Windows toast/notification about the overheat."""
        log_event("overheat_alert")
        with self._lock:
            temps = [t["temp"] for t in self.temps if t["temp"] is not None]
        peak = max(temps) if temps else None
        try:
            if peak is not None:
                self._notify(tr("alert.body", peak=peak),
                             title=tr("alert.title", peak=peak))
            else:
                self._notify(tr("alert.body.no_peak"),
                             title=tr("alert.title.no_peak"))
        except Exception:
            pass  # notifications are best-effort

    def quit(self, *_):
        log_event("shutdown")
        """Exit: ask the graph thread to close, flush history, stop the tray.

        No tkinter call is ever made from this (menu) thread -- the graph
        window closes itself through its own _tick loop within one refresh
        interval, and we wait for that before stopping the icon.
        """
        with self._lock:
            graph_open = self._graph_win is not None
            self._shutdown_requested = True
        if graph_open:
            self._graph_done.wait(3.0)  # graph thread closes itself; bounded
        # flush any unrecorded history points so nothing is lost
        if KEEP_HISTORY:
            with self._lock:
                points = list(self.history)
            save_history(points)
        _history24_save(self.history24)
        _history24_save_state(self._h24_state)
        try:
            self.icon.remove_notification()
        except Exception:
            pass
        self.icon.stop()

    # ---- history recording ----
    def _record(self, temp):
        """Append one sample to the in-memory history and flush to CSV."""
        if not KEEP_HISTORY:
            return
        now = time.time()
        with self._lock:
            self.history.append((now, temp))
            while len(self.history) > HISTORY_MAX_POINTS:
                self.history.pop(0)
            cutoff = now - HISTORY_SECONDS
            while self.history and self.history[0][0] < cutoff:
                self.history.pop(0)
            flush = now - self._last_save >= HISTORY_SAVE_INTERVAL
        if flush:
            self._last_save = now
            save_history(list(self.history))

    # ---- background polling ----
    def _auto_weekly_report(self):
        """Write the weekly report into the configured folder (no open) and
        toast once. Runs on a worker thread - never blocks the poll loop."""
        try:
            dest = SETTINGS.get("weekly_report_dir") or ""
            path = write_weekly_report(dest or None)
            if path:
                self._notify(tr("notify.weekly_saved", path=path),
                             tr("health.report"))
        except Exception:
            log_event("weekly_report_auto_failed")

    def poll_loop(self):
        while True:
            self.update()
            # auto-update: check right after start, then every N minutes
            interval = SETTINGS.get("update_check_interval_minutes", 360) * 60
            if (SETTINGS.get("check_updates")
                    and time.time() - self._last_update_check >= interval):
                self._last_update_check = time.time()
                threading.Thread(target=self._check_updates, daemon=True).start()
            # weekly auto-report: at most once per 7 days (cheap check)
            if SETTINGS.get("weekly_report_enabled"):
                if time.time() - self._last_weekly_report >= 7 * 86400:
                    self._last_weekly_report = time.time()
                    threading.Thread(target=self._auto_weekly_report,
                                     daemon=True).start()
            # sleep in small slices so a saved poll interval applies at once
            target = max(1, int(SETTINGS.get("poll_seconds", POLL_SECONDS)))
            for _ in range(target * 4):
                time.sleep(0.25)

    def update(self):
        temps = read_temps()
        with self._lock:
            self.temps = temps
        if not temps:
            self.icon.icon = make_icon("--", UNKNOWN)
            self.icon.title = _tooltip_text([])
            return
        # hottest SSD drives the icon
        valid = [t["temp"] for t in temps if t["temp"] is not None]
        hottest = max(valid) if valid else None
        if hottest is not None:
            self._record(hottest)        # overheat alert: sustained >= threshold with a cooldown between alerts
        self._smart_watch(temps)
        try:
            append_daily_health(temps)   # one row/day per disk, cheap no-op
            # minute-resolution long-tail history for the 24 h view
            pts, st = update_history24(self.history24, time.time(),
                                       self._h24_state, temps=temps)
            self.history24 = pts
            self._h24_state = st
        except Exception:
            pass
        self._alert_drive(hottest, time.time())

        text = str(hottest) if hottest is not None else "--"
        color = temp_color(hottest) if hottest is not None else UNKNOWN
        size = int(SETTINGS.get("icon_size", 64))
        hc = bool(SETTINGS.get("high_contrast_icon"))
        self.icon.icon = make_icon(text, color, size=size, high_contrast=hc)
        self.icon.title = _tooltip_text(temps)
        # one extra tray icon per additional internal SSD
        self._sync_extra_icons(temps)

    def run(self):
        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.icon.run()

def run_unattended_update():
    """--update-now: install a newer release without any UI.

    Downloads + SHA-256-verifies the setup exe, starts the cmd shim that
    waits for this process to release the AppMutex, then exits so the
    silent install can proceed (/RESTARTAPPLICATIONS brings the app back).
    """
    repo = SETTINGS.get("github_repo") or DEFAULT_SETTINGS["github_repo"]
    prerelease = bool(SETTINGS.get("update_channel") == "pre-release")
    release = fetch_latest_release(repo, include_prereleases=prerelease)
    if not release:
        print("UPDATE-RESULT: no release information")
        sys.exit(1)
    url, version = select_release_asset(release, prefer_prerelease=prerelease)
    if not url or not is_newer_version(version) or version_is_broken(version):
        if url and version_is_broken(version):
            print(f"UPDATE-RESULT: {version} previously rolled back - skipped")
            sys.exit(0)
        print(f"UPDATE-RESULT: no update available (latest={version or '?'})")
        sys.exit(0)
    print(f"UPDATE-RESULT: update available {version}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ssd-temp-monitor"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        sums_url = url.rsplit("/", 1)[0] + "/SHA256SUMS.txt"
        req = urllib.request.Request(sums_url, headers={"User-Agent": "ssd-temp-monitor"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            sums_text = resp.read().decode("utf-8", "replace")
        if not verify_asset(data, sums_text, url.rsplit("/", 1)[1]):
            print("UPDATE-RESULT: checksum mismatch - aborted")
            sys.exit(1)
        dest = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                            f"ssd_temp_monitor_setup_{version}.exe")
        with open(dest, "wb") as f:
            f.write(data)
    except Exception as exc:
        print(f"UPDATE-RESULT: download failed ({exc})")
        sys.exit(1)
    print("UPDATE-RESULT: checksum verified - handing over to installer")
    stage_backup_for_rollback(target_version=version)
    shim = build_update_shim(dest, restart_path=_own_restart_path())
    subprocess.Popen(["cmd", "/c", shim],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    sys.exit(0)  # frees the mutex; the shim then runs the silent install


def main():
    if not acquire_single_instance():
        # Duplicate start: exit code 2. A message box is shown for human
        # users; the --duplicate-silent flag (or SSD_TEMP_SILENT_DUPLICATE=1)
        # suppresses it for automated tests. NOTE: UAC elevation does NOT
        # forward the parent's environment, which is why the flag exists.
        if (os.environ.get("SSD_TEMP_SILENT_DUPLICATE") != "1"
                and "--duplicate-silent" not in sys.argv):
            ctypes.windll.user32.MessageBoxW(
                None, tr("duplicate.body"), tr("mb.title"),
                0x40 | 0x40000 | 0x10000,  # info icon | topmost | set foreground
            )
        sys.exit(2)
    if not is_admin():
        relaunch_elevated()
    if "--update-now" in sys.argv:
        # unattended update: check for a newer release and, if one exists,
        # download, verify and install it with no tray UI (CI/e2e friendly)
        run_unattended_update()
    cleanup_stale_mei()
    heal_autostart_value()
    begin_healthy_session()
    App().run()


if __name__ == "__main__":
    main()
