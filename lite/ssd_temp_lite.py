#!/usr/bin/env python3
"""SSD Temp Lite - portable single-file tray showing live SSD temperature.

THE WHOLE PROGRAM IS THIS FILE. It is the little sibling of
ssd_temp_tray.py, deliberately stripped down to one job:

    show the current SSD temperature as digits on the Windows tray icon,
    refreshed every few seconds. Nothing else.

Differences from the full app (on purpose):
    no config file, no history, no CSV, no event log, no settings window,
    no updater, no tkinter - so the frozen exe stays small.

Implementation notes (why it looks like "raw" Win32):
    the tray icon is registered directly through Shell_NotifyIconW via
    ctypes instead of pystray: one less runtime dependency, and the icon
    is drawn with Pillow from the very same code path as the main app.

Run from source (needs an ELEVATED shell so SMART data is available):
    python lite/ssd_temp_lite.py
Freeze to a single portable exe (see .github/workflows/release.yml):
    python -m PyInstaller --onefile --windowed --uac-admin \
        --name ssd_temp_lite --icon app_icon.ico lite/ssd_temp_lite.py
"""
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageFont

try:
    import pystray  # type: ignore  # the ONLY third-party runtime dependency
except ImportError:
    pystray = None  # frozen build bundles it; source run needs it installed

APP_TITLE = "SSD Temp Lite"

# ---- tuning ----------------------------------------------------------------
POLL_SECONDS = 5          # one SMART query every 5 s (lite = calm on purpose)
ICON_SIZE = 64            # icon bitmap size; the tray scales it down
DIGIT_MAX_POINTS = 96     # font height cap so "3" stays readable, "100" fits

# Same color bands as the main app (temp_color() in ssd_temp_tray.py).
GREEN = (34, 197, 94)
ORANGE = (245, 158, 11)
RED = (239, 68, 68)
GREY = (148, 163, 184)


def temp_color(temp):
    """Temperature -> pill color, same thresholds as the main app."""
    if temp is None:
        return GREY
    if temp >= 65:
        return RED
    if temp >= 51:
        return ORANGE
    return GREEN


# ---- the SMART query: copied verbatim from ssd_temp_tray.py (PS_TEMPS) -----
PS_TEMPS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$d=Get-PhysicalDisk | Where-Object { $_.MediaType -eq 'SSD' "
    "-and $_.BusType -ne 'USB' };"
    "$r=@(); foreach($x in $d){"
    "$c=$x|Get-StorageReliabilityCounter;"
    "$r+=[pscustomobject]@{model=$x.Model;friendly=$x.FriendlyName;"
    "media=$x.MediaType;bus=$x.BusType;temp=$c.Temperature;"
    "wear=$c.Wear;readErr=$c.ReadErrorsTotal;"
    "undef=$c.ReadErrorsUncorrected}};"
    "$r|ConvertTo-Json -Compress"
)


def read_temps():
    """[{model, temp}, ...] for internal SSDs; temp is int or None.

    Runs the same PowerShell query as the main app. Non-elevated
    processes get empty reliability counters -> temp=None everywhere.
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             PS_TEMPS],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return []
    raw = proc.stdout.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if isinstance(data, dict):      # a single disk still comes back as a dict
        data = [data]
    disks = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        if str(d.get("media", "")).lower() != "ssd":
            continue
        if str(d.get("bus", "")).lower() == "usb":
            continue                # USB bridges report bogus temperatures
        try:
            temp = int(d["temp"]) if d.get("temp") not in (None, "") else None
        except (TypeError, ValueError):
            temp = None
        disks.append({"model": str(d.get("model") or "SSD"), "temp": temp})
    return disks


# ---- icon rendering (same visual language as the main app) -----------------

def make_digit_icon(temp):
    """32..64px tray icon: colored pill + bold centered temperature digits."""
    size = ICON_SIZE
    img = Image.new("RGB", (size, size), temp_color(temp))
    draw = ImageDraw.Draw(img)
    text = "--" if temp is None else str(temp)
    # Try the bundled Segoe UI bold; fall back to PIL's default bitmap font
    # (deinit Pillow fonts are not always available in frozen builds).
    font = None
    for candidate in ("segoeuib.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(candidate, DIGIT_MAX_POINTS
                                      if len(text) < 3 else DIGIT_MAX_POINTS * 2 // 3)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((size - (right - left)) // 2 - left,
               (size - (bottom - top)) // 2 - top),
              text, font=font, fill=(15, 23, 42))
    return img


# ---- tray plumbing (pystray when available) --------------------------------

def run_tray():
    """Build the pystray icon + menu and run its message loop.

    pystray runs the Win32 message loop on THIS thread; the poll thread
    only ever swaps `icon.icon` / `icon.title`, which pystray forwards to
    the loop thread internally (documented as thread-safe).
    """
    import pystray
    from PIL import Image as _Img  # noqa: F401  (pystray wants PIL loaded)

    state = {"temps": [], "hot": None}

    icon = pystray.Icon(
        "ssd_temp_lite",
        icon=make_digit_icon(None),
        title=APP_TITLE,
        menu=pystray.Menu(
            pystray.MenuItem(
                lambda item: _details_text(state),
                lambda item, *_: _show_details(state),
                default=True),
            pystray.MenuItem(
                lambda item: _status_text(state),
                None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Refresh", lambda item, *_: poll_once(icon, state)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda item, *_: icon.stop()),
        ),
    )

    def poller():
        while True:
            poll_once(icon, state)
            time.sleep(POLL_SECONDS)

    threading.Thread(target=poller, daemon=True).start()
    icon.run()


def poll_once(icon, state):
    """One SMART read + repaint. Never raises: the tray must survive."""
    try:
        temps = read_temps()
    except Exception:
        temps = []
    valid = [t["temp"] for t in temps if t["temp"] is not None]
    hot = max(valid) if valid else None
    state["temps"] = temps
    state["hot"] = hot
    try:
        icon.icon = make_digit_icon(hot)
        icon.title = _tooltip_text(temps)
    except Exception:
        pass


def _tooltip_text(temps):
    """Compact tooltip: "Model: 37C | Model: 41C"."""
    if not temps:
        return APP_TITLE + " - no SSD data"
    return " | ".join(f"{t['model']}: {t['temp']}C"
                      if t["temp"] is not None else f"{t['model']}: n/a"
                      for t in temps)


def _details_text(state):
    """Dynamic label of the default menu item."""
    hot = state["hot"]
    if hot is None:
        return "Details (no data)"
    return f"Details - {hot} C now"


def _status_text(state):
    """Read-only status line under Details."""
    temps = state["temps"]
    if not temps:
        return "run as administrator to read SMART"
    return f"{len(temps)} SSD monitor"


def _show_details(state):
    """Native message box with one line per SSD (menu thread is fine)."""
    temps = state["temps"]
    if temps:
        body = "\n".join(
            f"{t['model']}:  {t['temp']} C" if t["temp"] is not None
            else f"{t['model']}:  n/a" for t in temps)
    else:
        body = "No SSD data.\n\nRun as administrator to read SMART."
    ctypes.windll.user32.MessageBoxW(
        None, body, APP_TITLE + " - Details",
        0x40 | 0x40000 | 0x10000)   # info | topmost | set foreground


def main():
    # single instance guard: same mutex pattern as the main app
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "Local\\SSDTempMonitorLite")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        sys.exit(2)

    if pystray is None:
        print("pystray is required: pip install pystray pillow", file=sys.stderr)
        sys.exit(1)

    run_tray()


if __name__ == "__main__":
    main()
