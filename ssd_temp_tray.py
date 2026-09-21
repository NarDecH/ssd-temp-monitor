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

import csv
import ctypes
import hashlib
import json
import os
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
APP_VERSION = "1.8.1"          # keep in sync with setup.iss #define MyAppVersion
UPDATE_CHECK_INTERVAL = 6 * 3600  # fallback only; poll_loop reads SETTINGS

GREEN = "#22c55e"
ORANGE = "#f59e0b"
RED = "#ef4444"
UNKNOWN = "#6b7280"
ACCENT = "#38bdf8"

# temperature sanity range: real SMART readings always fall inside this
TEMP_MIN, TEMP_MAX = -20, 100

# ---- history recording ----
# ---- settings (persisted to %APPDATA%\SSDTempMonitor\config.json) ----
CONFIG_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "SSDTempMonitor", "config.json")
DEFAULT_SETTINGS = {
    "poll_seconds": 1,
    "alert_threshold": 65,
    "alert_sustain_seconds": 30,
    "alert_cooldown_minutes": 5,
    "history_minutes": 30,
    "record_history": False,
    "multi_disk_icons": True,
    "check_updates": True,
    "github_repo": "NarDech/ssd-temp-monitor",
    "update_channel": "stable",              # or "pre-release"
    "update_check_interval_minutes": 360,     # auto-check every N minutes
}


def _validate_settings(cfg):
    """Keep known keys only, coerce to int/bool and clamp to safe ranges."""
    out = {k: cfg.get(k, d) for k, d in DEFAULT_SETTINGS.items()}
    out["poll_seconds"] = min(60, max(1, int(out["poll_seconds"])))
    out["alert_threshold"] = min(90, max(40, int(out["alert_threshold"])))
    out["alert_sustain_seconds"] = min(600, max(0, int(out["alert_sustain_seconds"])))
    out["alert_cooldown_minutes"] = min(120, max(1, int(out["alert_cooldown_minutes"])))
    out["history_minutes"] = min(240, max(5, int(out["history_minutes"])))
    out["record_history"] = bool(out["record_history"])
    out["update_channel"] = ("pre-release" if out["update_channel"] == "pre-release"
                             else "stable")
    try:
        out["update_check_interval_minutes"] = min(
            1440, max(5, int(out["update_check_interval_minutes"])))
    except (TypeError, ValueError):
        out["update_check_interval_minutes"] = \
            DEFAULT_SETTINGS["update_check_interval_minutes"]
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
        with open(path, encoding="utf-8") as f:
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
HISTORY_FILE = os.path.join(
    os.environ.get("TEMP", os.path.expanduser("~")), "ssd_temp_history.csv"
)
_env_history = os.environ.get("SSD_TEMP_RECORD_HISTORY")
KEEP_HISTORY = (SETTINGS["record_history"] if _env_history is None
                else _env_history == "1")
POLL_SECONDS = SETTINGS["poll_seconds"]

# ---- overheat alert (from settings) ----
ALERT_THRESHOLD = SETTINGS["alert_threshold"]        # °C
ALERT_SUSTAIN_SECONDS = SETTINGS["alert_sustain_seconds"]
ALERT_COOLDOWN_SECONDS = SETTINGS["alert_cooldown_minutes"] * 60

# ---- live graph window ----
GRAPH_W, GRAPH_H, GRAPH_PAD = 680, 320, 50
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
            None, "Administrator rights are required to read SSD temperature.",
            "SSD Temp Monitor", 0x10 | 0x40000 | 0x10000  # error icon | topmost | set foreground
        )
    sys.exit(0)


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
    "media=$x.MediaType;bus=$x.BusType;temp=$c.Temperature}};"
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
        }
        disks.append(disk)
    return disks


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
        {"model": d["model"], "temp": d["temp"]}
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


def _parse_version(v):
    """'v1.2.3-beta' -> (1, 2, 3) for simple numeric comparison."""
    try:
        core = str(v).strip().lstrip("vV").split("-")[0]
        return tuple(int(x) for x in core.split(".")[:3])
    except ValueError:
        return (0,)


def is_newer_version(remote, local=APP_VERSION):
    """True when the remote version string is strictly newer than ours."""
    try:
        return _parse_version(remote) > _parse_version(local)
    except Exception:
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


def fetch_latest_release(repo):
    """Query the GitHub Releases API. Returns a release dict or None.

    Network and API errors are swallowed and reported as 'no release'
    so a missing/broken connection can never break the poll loop.
    """
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
            return json.loads(resp.read().decode("utf-8"))
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
    fd, path = tempfile.mkstemp(prefix="ssd_update_", suffix=".cmd")
    with os.fdopen(fd, "w") as f:
        f.write(
            "@echo off\r\n"
            "rem SSD Temperature Monitor update shim: wait for the app to\r\n"
            "rem exit, then run the installer (AppMutex must be free).\r\n"
            ":wait\r\n"
            # absolute paths: never resolve `find` to GNU find from a
            # Git-bash PATH, which would silently break the pipeline
            # (%% -> literal % for the cmd %SystemRoot% variable)
            '%%SystemRoot%%\\System32\\tasklist.exe /FI "IMAGENAME eq %s" 2>nul '
            '| %%SystemRoot%%\\System32\\find.exe /I "%s" >nul ' % (app, app)
            + "&& (%SystemRoot%\\System32\\ping.exe -n 2 127.0.0.1 >nul "
              "& goto wait)\r\n"
            "rem hand over to the installer; /CLOSEAPPLICATIONS makes it\r\n"
            "rem close this app and restart it afterwards (RestartManager)\r\n"
            + ('"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART '
               "/CLOSEAPPLICATIONS /RESTARTAPPLICATIONS\r\n" % installer_path)
            # a silent install skips the postinstall launch, so bring the
            # updated app back ourselves (only when the install succeeded)
            + (f'if not errorlevel 1 start "" "{restart_path}"\r\n'
               if restart_path else "")
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


def make_icon(text, color):
    key = (text, color)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded background
    d.rounded_rectangle([2, 2, ICON_SIZE - 2, ICON_SIZE - 2], radius=14, fill=color)
    try:
        font = ImageFont.truetype("arial.ttf", 34 if len(text) <= 2 else 26)
    except OSError:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((ICON_SIZE - w) / 2 - bbox[0], (ICON_SIZE - h) / 2 - bbox[1]),
           text, font=font, fill="white")
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


class App:
    def __init__(self):
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
        self._pending_update = None
        self._last_update_check = 0.0
        self.icon = pystray.Icon(
            "ssd_temp",
            icon=make_icon("--", UNKNOWN),
            title="SSD Temperature Monitor",
            menu=pystray.Menu(
                pystray.MenuItem("Show details", self.show_details, default=True),
                pystray.MenuItem("Show temperature graph", self.show_graph),
                pystray.MenuItem("Show all disks (debug)", self.show_disks),
                pystray.MenuItem("Refresh now", self.refresh),
                pystray.MenuItem("Check for updates...", self.check_updates_now),
                pystray.MenuItem("Settings...", self.show_settings),
                pystray.MenuItem("About", self.show_about),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Record history", self.toggle_history,
                                 checked=lambda item: KEEP_HISTORY),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self.quit),
            ),
        )
        self._lock = threading.Lock()

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

    def show_graph(self, *_):
        # MUST go through _spawn_once like the other windows: running the tk
        # event loop inside pystray's menu-callback thread blocks the tray
        # (Exit dead) and calling tk methods from a second menu click corrupts
        # the tk interpreter -> the window stops processing events and can
        # never be closed. If already open, raise it via Win32 instead.
        already = self._graph_open
        self._spawn_once("_graph_open", self._graph_window)
        if already:
            self._raise_window("SSD Temperature - History")

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
            lines.append(("No SSD temperature data (run as Administrator).", UNKNOWN))
        else:
            for t in temps:
                if t["temp"] is not None:
                    lines.append((f"{t['model']}:  {t['temp']} °C", temp_color(t["temp"])))
                else:
                    lines.append((f"{t['model']}:  n/a", UNKNOWN))

        import tkinter as tk
        root = tk.Tk()
        root.title("SSD Temperature")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=24, pady=12)
        frame.pack()
        for text, color in lines:
            tk.Label(frame, text=text, font=("Segoe UI", 13, "bold"),
                     fg=color).pack(anchor="w", pady=2)
        tk.Button(frame, text="Close", command=root.destroy,
                  font=("Segoe UI", 10)).pack(pady=(10, 0))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Return>", lambda e: root.destroy())
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()

    def _graph_window(self):
        """Create and own the history graph window (runs in its own thread).

        After mainloop() returns, the thread that created the tk widgets is
        gone; we explicitly NULL out _graph_win so the state stays honest.
        """
        import tkinter as tk
        win = tk.Tk()
        win.title("SSD Temperature - History")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        W, H, PAD = GRAPH_W, GRAPH_H, GRAPH_PAD
        canvas = tk.Canvas(win, width=W, height=H, bg="#0f172a",
                           highlightthickness=0)
        canvas.pack(padx=12, pady=(12, 4))
        tk.Button(win, text="Close", command=win.destroy,
                  font=("Segoe UI", 10)).pack(pady=(2, 10))
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
        with self._lock:
            self._graph_win = None
        self._graph_done.set()

    def _draw_graph(self, canvas, W, H, PAD):
        import tkinter as tk
        Y_LO, Y_HI = GRAPH_Y_LO, GRAPH_Y_HI
        canvas.delete("all")
        data = self._snapshot("history")
        if not data:
            canvas.create_text(
                W / 2, H / 2, fill="#94a3b8", font=("Segoe UI", 12),
                text="No history recorded yet.\n"
                     "Enable 'Record history' in the tray menu.",
                justify="center")
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

        # grid + y-axis labels
        for gt in range(20, Y_HI, 20):
            yy = y(gt)
            canvas.create_line(PAD, yy, W - PAD, yy, fill="#1e293b")
            canvas.create_text(PAD - 8, yy, anchor="e", fill="#64748b",
                               font=("Segoe UI", 9), text=f"{gt}°")
        canvas.create_line(PAD, H - PAD, W - PAD, H - PAD, fill="#334155")
        canvas.create_text(W - PAD, H - PAD + 14, anchor="ne", fill="#64748b",
                           font=("Segoe UI", 9),
                           text=f"last {span / 60:.0f} min")

        coords = []
        for t, temp in pts:
            coords.extend((x(t), y(temp)))
        canvas.create_line(*coords, fill=ACCENT, width=2, joinstyle="round")

        temps = [temp for _, temp in pts]
        lo, hi = min(temps), max(temps)
        avg = sum(temps) / len(temps)
        canvas.create_text(
            PAD, 12, anchor="nw", font=("Segoe UI", 11, "bold"), fill="#e2e8f0",
            text=f"min {lo}°C   max {hi}°C   avg {avg:.1f}°C   ({len(pts)} samples)")

    def _disks_window(self):
        import tkinter as tk
        disks = list_all_disks()
        root = tk.Tk()
        root.title("SSD Temperature - All Disks (debug)")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=24, pady=12)
        frame.pack()
        tk.Label(frame, text="✓ shown on icon    ✗ filtered out",
                 font=("Segoe UI", 9), fg="#64748b").pack(anchor="w")
        if not disks:
            tk.Label(frame, text="No disks found.",
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
        tk.Button(frame, text="Close", command=root.destroy,
                  font=("Segoe UI", 10)).pack(pady=(10, 0))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Return>", lambda e: root.destroy())
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()

    def _settings_window(self):
        """Settings dialog: edit values and save to config.json."""
        import tkinter as tk
        from tkinter import ttk, messagebox

        with self._lock:
            current = dict(SETTINGS)

        root = tk.Tk()
        root.title("SSD Temperature - Settings")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=22, pady=14)
        frame.pack()

        rows = [
            ("Poll interval (seconds, 1-60)", "poll_seconds", 1, 60),
            ("Alert threshold (°C, 40-90)", "alert_threshold", 40, 90),
            ("Alert sustain (seconds, 0-600)", "alert_sustain_seconds", 0, 600),
            ("Alert cooldown (minutes, 1-120)", "alert_cooldown_minutes", 1, 120),
            ("History window (minutes, 5-240)", "history_minutes", 5, 240),
            ("Update check interval (minutes, 5-1440)",
             "update_check_interval_minutes", 5, 1440),
        ]
        vars_ = {}
        for i, (label, key, lo, hi) in enumerate(rows):
            tk.Label(frame, text=label, font=("Segoe UI", 10),
                     anchor="w").grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(current[key]))
            spin = ttk.Spinbox(frame, from_=lo, to=hi, width=8,
                               textvariable=var)
            spin.grid(row=i, column=1, padx=(14, 0), pady=3)
            vars_[key] = var

        record_var = tk.BooleanVar(value=current["record_history"])
        ttk.Checkbutton(frame, text="Record history on startup",
                        variable=record_var).grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(8, 0))

        tk.Label(frame, text="Update channel", font=("Segoe UI", 10),
                 anchor="w").grid(row=len(rows) + 1, column=0, sticky="w",
                                  pady=3)
        channel_var = tk.StringVar(value=current.get("update_channel", "stable"))
        ttk.Combobox(frame, textvariable=channel_var, width=14,
                     values=("stable", "pre-release"), state="readonly").grid(
            row=len(rows) + 1, column=1, padx=(14, 0), pady=3)

        note = tk.Label(
            frame,
            text="Poll interval takes effect after restarting the app.",
            font=("Segoe UI", 8), fg="#64748b")
        note.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="w",
                  pady=(4, 0))

        def on_save():
            global POLL_SECONDS, KEEP_HISTORY
            try:
                for key, var in vars_.items():
                    current[key] = int(var.get())
                current["record_history"] = bool(record_var.get())
                current["update_channel"] = channel_var.get()
            except ValueError:
                messagebox.showerror("SSD Temp Monitor",
                                     "Please enter whole numbers only.",
                                     parent=root)
                return
            validated = _validate_settings(current)
            SETTINGS.clear()
            SETTINGS.update(validated)
            POLL_SECONDS = SETTINGS["poll_seconds"]
            if os.environ.get("SSD_TEMP_RECORD_HISTORY") is None:
                KEEP_HISTORY = SETTINGS["record_history"]
            if save_settings(validated):
                root.destroy()
            else:
                messagebox.showerror(
                    "SSD Temp Monitor",
                    f"Could not write {CONFIG_FILE}", parent=root)

        btns = tk.Frame(frame)
        btns.grid(row=len(rows) + 3, column=0, columnspan=2, pady=(12, 0))
        tk.Button(btns, text="Save", width=10, command=on_save,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        tk.Button(btns, text="Cancel", width=10, command=root.destroy,
                  font=("Segoe UI", 10)).pack(side="left", padx=4)
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()

    # ---- auto-update ----
    def _check_updates(self, manual=False):
        """Look for a newer GitHub release; notify / offer to install.

        Runs on a worker thread; never raises. With no update available the
        user only sees a message when the check was manual.
        """
        repo = SETTINGS.get("github_repo") or DEFAULT_SETTINGS["github_repo"]
        release = fetch_latest_release(repo)
        if not release:
            if manual:
                self._notify("No update information available.",
                             "SSD Temp Monitor — Update")
            return
        prerelease = bool(SETTINGS.get("update_channel") == "pre-release")
        url, version = select_release_asset(release, prefer_prerelease=prerelease)
        if not url or not is_newer_version(version):
            if manual:
                self._notify(
                    f"You are running the latest version ({APP_VERSION}).",
                    "SSD Temp Monitor — Update")
            return
        # a newer release exists -> surface it
        self._notify(
            f"Version {version} is available ({APP_VERSION} installed).\n"
            "Right-click -> Check for updates... to install.",
            title="SSD Temp Monitor — update available")
        with self._lock:
            self._pending_update = (url, version)

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
        self._notify(f"Downloading {version}...", "SSD Temp Monitor")

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
                    self._notify("Checksum mismatch - update aborted.",
                                 "SSD Temp Monitor — Update")
                    return
                dest = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                                    f"ssd_temp_monitor_setup_{version}.exe")
                with open(dest, "wb") as f:
                    f.write(data)
                shim = build_update_shim(dest, restart_path=_own_restart_path())
            except Exception:
                self._notify("Update download failed.", "SSD Temp Monitor — Update")
                return
            self._notify(f"Installing {version}...", "SSD Temp Monitor")
            # exit so the installer can replace the exe; the shim waits for
            # us to let go of the AppMutex before starting the installer
            self.quit()
            code = wait_and_install(shim)
            if code in (0, 1002, 1004):
                return  # installer succeeded (or handed off to a restart)
            try:
                os.remove(shim)
            except OSError:
                pass
            ctypes.windll.user32.MessageBoxW(
                None,
                f"The update to {version} could not be installed\n"
                f"(installer exit code {code}).\n"
                "Download it manually from the About window.",
                "SSD Temp Monitor — Update", 0x10 | 0x40000 | 0x10000)

        threading.Thread(target=worker, daemon=True).start()

    def _about_window(self):
        """Small About dialog: version, repo link, update check button."""
        import tkinter as tk
        import webbrowser

        root = tk.Tk()
        root.title("About SSD Temperature Monitor")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=26, pady=14)
        frame.pack()
        tk.Label(frame, text="SSD Temperature Monitor",
                 font=("Segoe UI", 15, "bold"),
                 fg="#e2e8f0").pack(anchor="w")
        tk.Label(frame, text=f"Version {APP_VERSION}",
                 font=("Segoe UI", 10),
                 fg="#94a3b8").pack(anchor="w", pady=(2, 6))
        repo = SETTINGS.get("github_repo") or DEFAULT_SETTINGS["github_repo"]
        link = tk.Label(frame, text=f"github.com/{repo}",
                        font=("Segoe UI", 10, "underline"), fg="#38bdf8",
                        cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>",
                  lambda e: webbrowser.open(f"https://github.com/{repo}/releases/latest"))
        btns = tk.Frame(frame)
        btns.pack(pady=(12, 0))
        tk.Button(btns, text="Check for updates", width=16,
                  command=lambda: self.check_updates_now(),
                  font=("Segoe UI", 9)).pack(side="left", padx=4)
        tk.Button(btns, text="Close", width=10, command=root.destroy,
                  font=("Segoe UI", 9)).pack(side="left", padx=4)
        root.bind("<Escape>", lambda e: root.destroy())
        root.mainloop()

    def _notify(self, message, title):
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
                pystray.MenuItem("Show details", self.show_details, default=True),
                pystray.MenuItem("Exit", self.quit),
            ),
        )
        return icon

    @staticmethod
    def _update_extra_icon(icon, model, temp):
        icon.icon = make_icon(str(temp) if temp is not None else "--",
                              temp_color(temp))
        icon.title = f"{model}: {temp}C" if temp is not None else f"{model}: n/a"

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
        with self._lock:
            temps = [t["temp"] for t in self.temps if t["temp"] is not None]
        peak = max(temps) if temps else None
        try:
            self.icon.notify(
                f"SSD has been at {peak}°C for a while."
                if peak is not None else "SSD is overheating.",
                title=f"⚠ SSD overheat: {peak}°C" if peak is not None
                      else "⚠ SSD overheat",
            )
        except Exception:
            pass  # notifications are best-effort

    def quit(self, *_):
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
    def poll_loop(self):
        while True:
            self.update()
            # auto-update: check right after start, then every N minutes
            interval = SETTINGS.get("update_check_interval_minutes", 360) * 60
            if (SETTINGS.get("check_updates")
                    and time.time() - self._last_update_check >= interval):
                self._last_update_check = time.time()
                threading.Thread(target=self._check_updates, daemon=True).start()
            time.sleep(POLL_SECONDS)

    def update(self):
        temps = read_temps()
        with self._lock:
            self.temps = temps
        if not temps:
            self.icon.icon = make_icon("--", UNKNOWN)
            self.icon.title = "SSD Temperature Monitor - no data"
            return
        # hottest SSD drives the icon
        valid = [t["temp"] for t in temps if t["temp"] is not None]
        hottest = max(valid) if valid else None
        if hottest is not None:
            self._record(hottest)        # overheat alert: sustained >= threshold with a cooldown between alerts
        self._alert_drive(hottest, time.time())

        text = str(hottest) if hottest is not None else "--"
        color = temp_color(hottest) if hottest is not None else UNKNOWN
        self.icon.icon = make_icon(text, color)
        self.icon.title = " | ".join(
            f"{t['model']}: {t['temp']}C" if t["temp"] is not None else f"{t['model']}: n/a"
            for t in temps
        )
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
    release = fetch_latest_release(repo)
    if not release:
        print("UPDATE-RESULT: no release information")
        sys.exit(1)
    prerelease = bool(SETTINGS.get("update_channel") == "pre-release")
    url, version = select_release_asset(release, prefer_prerelease=prerelease)
    if not url or not is_newer_version(version):
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
                None, "SSD Temperature Monitor is already running\n"
                      "(check the system tray).",
                "SSD Temp Monitor", 0x40 | 0x40000 | 0x10000,  # info icon | topmost | set foreground
            )
        sys.exit(2)
    if not is_admin():
        relaunch_elevated()
    if "--update-now" in sys.argv:
        # unattended update: check for a newer release and, if one exists,
        # download, verify and install it with no tray UI (CI/e2e friendly)
        run_unattended_update()
    App().run()


if __name__ == "__main__":
    main()
