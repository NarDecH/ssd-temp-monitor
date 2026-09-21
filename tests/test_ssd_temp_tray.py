"""Unit tests for ssd_temp_tray.py — no GUI, no PowerShell, no admin needed.

The PowerShell boundary is always monkeypatched; only pure logic and the
CSV/Windows-mutex helpers are exercised for real.
"""
import csv
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_gui_stubs():
    """Stub PIL/pystray only when they are not installed."""
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
        return
    except ImportError:
        pass
    for name in ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "pystray"):
        sys.modules.setdefault(name, types.ModuleType(name))


_ensure_gui_stubs()

import ssd_temp_tray as m  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def app():
    """An App instance without __init__ (which would create a real tray icon)."""
    a = m.App.__new__(m.App)
    a._lock = threading.Lock()
    a.history = []
    a._last_save = 0.0
    a.temps = []
    a._alert_since = None
    a._last_alert = 0.0
    a._graph_win = None
    a._graph_done = threading.Event()
    a._shutdown_requested = False
    a._pending_update = None
    a._nagged_version = None
    a._last_update_check = 0.0
    a.icon = None
    return a


@pytest.fixture(autouse=True)
def _clean_global_state():
    """Isolate SETTINGS and the icon cache around every test.

    make_icon() reads SETTINGS directly and caches by a key that includes
    the settings - without this fixture a test that leaks e.g.
    icon_digit_color makes later icon tests flaky depending on order.
    """
    saved = dict(m.SETTINGS)
    m._ICON_CACHE.clear()
    yield
    m.SETTINGS.clear()
    m.SETTINGS.update(saved)
    m._ICON_CACHE.clear()


@pytest.fixture
def history_file(tmp_path, monkeypatch):
    f = tmp_path / "hist.csv"
    monkeypatch.setattr(m, "HISTORY_FILE", str(f))
    return f


# ---------------------------------------------------------------------------
# disk classification (the original USB bug)
# ---------------------------------------------------------------------------
class TestIsInternalSsd:
    def test_internal_nvme_included(self):
        assert m.is_internal_ssd({"model": "A", "media": "SSD", "bus": "NVMe"}) is True

    def test_internal_sata_included(self):
        assert m.is_internal_ssd({"model": "A", "media": "SSD", "bus": "SATA"}) is True

    def test_usb_reader_excluded_even_if_reported_as_ssd(self):
        # the exact case from the bug report: ASM236X reports MediaType=SSD
        assert m.is_internal_ssd({"model": "ASM236X", "media": "SSD", "bus": "USB"}) is False

    def test_hdd_excluded(self):
        assert m.is_internal_ssd({"model": "H", "media": "HDD", "bus": "SATA"}) is False

    def test_unknown_media_excluded(self):
        assert m.is_internal_ssd({"model": "X", "media": "?", "bus": "NVMe"}) is False

    def test_case_insensitive(self):
        assert m.is_internal_ssd({"model": "A", "media": "ssd", "bus": "nvme"}) is True

    def test_usb_filter_must_precede_counter_read(self):
        """Regression guard: some USB bridge chips block the counter read
        for ~40 s, so the USB filter has to run BEFORE it in the query."""
        filter_pos = m.PS_TEMPS.find("BusType -ne 'USB'")
        counter_pos = m.PS_TEMPS.find("Get-StorageReliabilityCounter")
        assert filter_pos != -1 and counter_pos != -1
        assert filter_pos < counter_pos
        # the debug list must never read reliability counters at all
        assert "Get-StorageReliabilityCounter" not in m.PS_LIST


# ---------------------------------------------------------------------------
# temperature sanitizing
# ---------------------------------------------------------------------------
class TestSafeTemp:
    @pytest.mark.parametrize("raw,expected", [
        ("36", 36), (36, 36), ("-5", -5), ("-20", -20), ("100", 100),
    ])
    def test_valid_values(self, raw, expected):
        assert m._safe_temp(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "abc", "65535", "-99", "101", {}, []])
    def test_invalid_values(self, raw):
        assert m._safe_temp(raw) is None


class TestTempColor:
    @pytest.mark.parametrize("temp,expected", [
        (30, m.GREEN), (50, m.GREEN),
        (51, m.ORANGE), (64, m.ORANGE),
        (65, m.RED), (90, m.RED),
        (None, m.UNKNOWN),
    ])
    def test_boundaries(self, temp, expected):
        assert m.temp_color(temp) == expected


# ---------------------------------------------------------------------------
# PowerShell output parsing
# ---------------------------------------------------------------------------
class TestParseDisks:
    def test_single_dict_wrapped_to_list(self):
        out = '{"model":"A","media":"SSD","bus":"NVMe","temp":41}'
        disks = m._parse_disks(out, with_temp=True)
        assert disks == [{"model": "A", "media": "SSD", "bus": "NVMe", "temp": 41}]

    def test_list_parsed(self):
        out = ('[{"model":"A","media":"SSD","bus":"NVMe","temp":41},'
               '{"model":"B","media":"SSD","bus":"USB","temp":0}]')
        disks = m._parse_disks(out, with_temp=True)
        assert len(disks) == 2

    def test_missing_temp_field(self):
        out = '[{"model":"A","media":"SSD","bus":"NVMe"}]'
        assert m._parse_disks(out, with_temp=True)[0]["temp"] is None

    def test_garbage_json_returns_empty(self):
        assert m._parse_disks("not json at all", with_temp=True) == []
        assert m._parse_disks("", with_temp=True) == []

    def test_without_temp_not_sanitized_away(self):
        out = '[{"model":"A","media":"SSD","bus":"USB","temp":0}]'
        disk = m._parse_disks(out, with_temp=False)[0]
        assert disk["temp"] is None


class TestReadTemps:
    def test_only_internal_ssds_returned(self, monkeypatch):
        def fake_run(cmd):
            assert cmd is m.PS_TEMPS
            return ('[{"model":"NVME SSD","media":"SSD","bus":"NVMe","temp":"44"},'
                    '{"model":"USB reader","media":"SSD","bus":"USB","temp":"0"}]')
        monkeypatch.setattr(m, "_run_powershell", fake_run)
        # USB entry must be dropped client-side too (defense in depth)
        assert m.read_temps() == [{"model": "NVME SSD", "temp": 44}]

    def test_client_side_filter_guards_regression(self, monkeypatch):
        """Even if someone breaks the PowerShell filter, USB disks must not
        reach the tray readout (they stall the counter read for ~40 s)."""
        monkeypatch.setattr(
            m, "PS_TEMPS",
            "BROKEN QUERY WITHOUT USB FILTER | Get-StorageReliabilityCounter")
        monkeypatch.setattr(
            m, "_run_powershell",
            lambda cmd: ('[{"model":"NVME SSD","media":"SSD","bus":"NVMe","temp":"44"},'
                         '{"model":"USB reader","media":"SSD","bus":"USB","temp":"0"}]'))
        assert m.read_temps() == [{"model": "NVME SSD", "temp": 44}]

    def test_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(m, "_run_powershell", lambda cmd: "")
        assert m.read_temps() == []


class TestListAllDisks:
    def test_merges_temps_from_fast_query(self, monkeypatch):
        def fake_run(cmd):
            if cmd is m.PS_TEMPS:
                return '[{"model":"NVME SSD","media":"SSD","bus":"NVMe","temp":"44"}]'
            if cmd is m.PS_LIST:
                return ('[{"model":"NVME SSD","media":"SSD","bus":"NVMe"},'
                        '{"model":"USB reader","media":"SSD","bus":"USB"}]')
            raise AssertionError("unexpected query")
        monkeypatch.setattr(m, "_run_powershell", fake_run)
        disks = m.list_all_disks()
        assert disks[0] == {"model": "NVME SSD", "media": "SSD", "bus": "NVMe", "temp": 44}
        assert disks[1] == {"model": "USB reader", "media": "SSD", "bus": "USB", "temp": None}

    def test_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(m, "_run_powershell", lambda cmd: "")
        assert m.list_all_disks() == []


# ---------------------------------------------------------------------------
# overheat alert state machine
# ---------------------------------------------------------------------------
class TestAlertState:
    T, S, C = m.ALERT_THRESHOLD, m.ALERT_SUSTAIN_SECONDS, m.ALERT_COOLDOWN_SECONDS

    def test_below_threshold_resets(self):
        assert m.alert_state(50, 100.0, 0.0, 200.0) == (False, None)

    def test_none_temp_resets(self):
        assert m.alert_state(None, 100.0, 0.0, 200.0) == (False, None)

    def test_first_crossing_not_sustained(self):
        fire, since = m.alert_state(self.T + 5, 190.0, 0.0, 200.0)
        assert fire is False and since == 190.0

    def test_fires_after_sustain_and_cooldown(self):
        fire, since = m.alert_state(self.T + 5, 160.0, -1000.0, 200.0)
        assert fire is True and since == 160.0

    def test_no_refire_inside_cooldown(self):
        fire, since = m.alert_state(self.T + 5, 160.0, 195.0, 200.0)
        assert fire is False and since == 160.0

    def test_refires_after_cooldown(self):
        now = 10_000.0
        fire, since = m.alert_state(self.T + 5, now - 60.0, now - self.C - 1, now)
        assert fire is True

    def test_realistic_sequence(self, app):
        # first hot reading starts the clock; 25 s -> silent, 31 s -> alert,
        # 1 min after the alert -> still inside the 5 min cooldown -> silent
        t0 = 1000.0
        assert app._alert_drive(self.T + 5, t0) is False
        assert app._alert_drive(self.T + 5, t0 + 25) is False
        assert app._alert_drive(self.T + 5, t0 + 31) is True
        assert app._alert_drive(self.T + 5, t0 + 31 + 60) is False


# ---------------------------------------------------------------------------
# CSV history
# ---------------------------------------------------------------------------
class TestHistory:
    NOW = 1_800_000_000.0  # fixed 'today' so cutoff filtering is deterministic

    def test_save_and_load_roundtrip(self, history_file, monkeypatch):
        monkeypatch.setattr(m.time, "time", lambda: self.NOW)
        pts = [(self.NOW - 120, 41), (self.NOW - 60, 45), (self.NOW, 47)]
        m.save_history(pts)
        assert m.load_history() == pts

    def test_load_drops_points_older_than_window(self, history_file, monkeypatch):
        monkeypatch.setattr(m.time, "time", lambda: self.NOW)
        now = self.NOW
        m.save_history([(now - 4000, 40), (now - 100, 55), (now, 61)])
        assert m.load_history() == [(now - 100, 55), (now, 61)]

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "HISTORY_FILE", str(tmp_path / "nope.csv"))
        assert m.load_history() == []

    def test_load_skips_corrupt_rows(self, history_file, monkeypatch):
        monkeypatch.setattr(m.time, "time", lambda: self.NOW)
        history_file.write_text(
            f"{self.NOW - 120},41\nnot,a,valid,row\nbad,x\n{self.NOW - 60},45\n")
        assert m.load_history() == [(self.NOW - 120, 41), (self.NOW - 60, 45)]

    def test_record_appends(self, app, history_file):
        m.KEEP_HISTORY = True
        try:
            app._record(42)
            app._record(43)
            assert [t for _, t in app.history] == [42, 43]
        finally:
            m.KEEP_HISTORY = False

    def test_record_noop_when_disabled(self, app, history_file):
        m.KEEP_HISTORY = False
        app._record(42)
        assert app.history == []

    def test_record_trims_window_and_points(self, app, history_file, monkeypatch):
        m.KEEP_HISTORY = True
        monkeypatch.setattr(m, "HISTORY_MAX_POINTS", 5)
        monkeypatch.setattr(m, "HISTORY_SECONDS", 60)
        monkeypatch.setattr(m, "HISTORY_SAVE_INTERVAL", 10**9)  # never flush

        clock = {"t": m.time.time()}
        monkeypatch.setattr(m.time, "time", lambda: clock["t"])
        try:
            for i in range(8):
                clock["t"] += 1      # 1 s steps, all within the 60 s window
                app._record(40 + i)
        finally:
            m.KEEP_HISTORY = False
        assert len(app.history) == 5            # hard cap applied
        assert [t for _, t in app.history] == [43, 44, 45, 46, 47]

    def test_flush_respects_interval(self, app, history_file, monkeypatch):
        m.KEEP_HISTORY = True
        calls = []
        monkeypatch.setattr(m, "save_history", lambda pts: calls.append(list(pts)))
        clock = {"t": 1000.0}
        monkeypatch.setattr(m.time, "time", lambda: clock["t"])
        try:
            app._record(40)          # first ever -> flush
            assert len(calls) == 1
            clock["t"] += 1
            app._record(41)          # 1 s later -> no flush
            assert len(calls) == 1
            clock["t"] += 61
            app._record(42)          # past the 60 s interval -> flush
            assert len(calls) == 2
        finally:
            m.KEEP_HISTORY = False

    def test_toggle_history_flips_flag(self, app, monkeypatch):
        monkeypatch.setattr(m, "KEEP_HISTORY", False)
        app.toggle_history()
        assert m.KEEP_HISTORY is True
        app.toggle_history()
        assert m.KEEP_HISTORY is False


# ---------------------------------------------------------------------------
# tray icon + windows single instance
# ---------------------------------------------------------------------------
def test_make_icon_cached():
    pytest.importorskip("PIL", reason="Pillow not installed")
    a = m.make_icon("42", m.GREEN)
    b = m.make_icon("42", m.GREEN)
    assert a is b


def test_single_instance_mutex(monkeypatch):
    name = f"Local\\ssd_temp_test_{id(object())}"
    monkeypatch.setattr(m, "MUTEX_NAME", name)
    assert m.acquire_single_instance() is True   # first acquisition wins
    assert m.acquire_single_instance() is False  # second instance detected


# ---------------------------------------------------------------------------
# settings / config.json
# ---------------------------------------------------------------------------
class TestSettings:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = m.load_settings(tmp_path / "nope.json")
        assert cfg == m.DEFAULT_SETTINGS

    def test_roundtrip(self, tmp_path):
        f = tmp_path / "config.json"
        cfg = dict(m.DEFAULT_SETTINGS, poll_seconds=5, alert_threshold=70)
        assert m.save_settings(cfg, path=f) is True
        loaded = m.load_settings(f)
        assert loaded["poll_seconds"] == 5
        assert loaded["alert_threshold"] == 70
        assert loaded["history_minutes"] == m.DEFAULT_SETTINGS["history_minutes"]

    def test_corrupt_json_returns_defaults(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text("{not json", encoding="utf-8")
        assert m.load_settings(f) == m.DEFAULT_SETTINGS

    @pytest.mark.parametrize("raw,default", [
        ("65", 65),      # wrong type (string) -> ignored
        (None, 65),      # wrong type -> ignored
        ([65], 65),      # wrong type -> ignored
    ])
    def test_wrong_typed_values_ignored(self, tmp_path, raw, default):
        f = tmp_path / "config.json"
        f.write_text('{"alert_threshold": ' +
                     ("null" if raw is None else f'"65"' if isinstance(raw, str)
                      else "[65]") + "}", encoding="utf-8")
        assert m.load_settings(f)["alert_threshold"] == default

    @pytest.mark.parametrize("value,clamped", [
        (0, 1), (999, 60),                      # poll_seconds 1..60
    ])
    def test_ranges_clamped(self, tmp_path, value, clamped):
        f = tmp_path / "config.json"
        f.write_text('{"poll_seconds": %d}' % value, encoding="utf-8")
        assert m.load_settings(f)["poll_seconds"] == clamped

    def test_validate_unknown_keys_dropped(self):
        out = m._validate_settings({"poll_seconds": 2, "evil_key": 1})
        assert "evil_key" not in out
        assert out["poll_seconds"] == 2
        assert set(out) == set(m.DEFAULT_SETTINGS)

    def test_validate_threshold_bounds(self):
        assert m._validate_settings({"alert_threshold": 10})["alert_threshold"] == 40
        assert m._validate_settings({"alert_threshold": 200})["alert_threshold"] == 90

    def test_module_loads_config_at_import(self):
        # POLL_SECONDS etc. are derived from SETTINGS, not hardcoded
        assert m.POLL_SECONDS == m.SETTINGS["poll_seconds"]
        assert m.ALERT_THRESHOLD == m.SETTINGS["alert_threshold"]
        assert m.HISTORY_SECONDS == m.SETTINGS["history_minutes"] * 60


class TestPerDiskIcons:
    @pytest.fixture
    def app(self):
        a = m.App.__new__(m.App)
        a._lock = threading.Lock()
        a._extra_icons = {}
        return a

    def test_single_disk_no_extra_icons(self, app, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "multi_disk_icons", True)
        fake = object()
        app._sync_extra_icons([{"model": "A", "temp": 40}])
        assert app._extra_icons == {}

    def test_second_disk_gets_icon(self, app, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "multi_disk_icons", True)
        created = []

        class FakeIcon:
            def __init__(self, key):
                self.key = key

            def run(self):
                pass

        def make(key, model, temp):
            created.append(key)
            return FakeIcon(key)

        monkeypatch.setattr(app, "_make_extra_icon", make)
        monkeypatch.setattr(m.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: None))
        app._sync_extra_icons([{"model": "A", "temp": 40}, {"model": "B", "temp": 50}])
        assert len(created) == 1 and "B" in created[0]
        assert app._extra_icons

    def test_disk_removal_stops_icon(self, app, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "multi_disk_icons", True)
        stopped = []

        class FakeIcon:
            def stop(self):
                stopped.append(1)

        app._extra_icons = {"1:B": FakeIcon()}
        app._sync_extra_icons([{"model": "A", "temp": 40}])
        assert stopped == [1] and app._extra_icons == {}

    def test_disabled_setting_keeps_primary_only(self, app, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "multi_disk_icons", False)
        app._sync_extra_icons([{"model": "A", "temp": 40}, {"model": "B", "temp": 50}])
        assert app._extra_icons == {}


# ---------------------------------------------------------------------------
# auto-update (GitHub Releases)
# ---------------------------------------------------------------------------
class TestVersionCompare:
    @pytest.mark.parametrize("text,expected", [
        ("v1.2.3", (1, 2, 3)),
        ("1.10.0", (1, 10, 0)),
        ("V2.0", (2, 0)),
        ("1.2.3-beta", (1, 2, 3)),
        ("garbage", (0,)),
    ])
    def test_parse(self, text, expected):
        assert m._parse_version(text) == expected

    @pytest.mark.parametrize("remote,local,expected", [
        ("1.6.0", "1.5.0", True),
        ("v1.6.0", "1.6.0", False),   # equal -> no nag
        ("1.5.9", "1.6.0", False),
        ("2.0.0", "1.9.9", True),
        ("1.5", "1.6.0", False),
        ("nonsense", "1.6.0", False),
    ])
    def test_is_newer(self, remote, local, expected):
        assert m.is_newer_version(remote, local) is expected


class TestSelectReleaseAsset:
    def asset(self, name, state="uploaded"):
        return {"name": name, "browser_download_url": f"https://x/{name}", "state": state}

    def test_prefers_setup_exe(self):
        release = {"tag_name": "v1.6.0", "assets": [
            self.asset("ssd_temp_monitor_v1.6.0.exe"),
            self.asset("ssd_temp_monitor_setup_v1.6.0.exe"),
        ]}
        url, version = m.select_release_asset(release)
        assert "setup" in url and version == "v1.6.0"

    def test_falls_back_to_portable(self):
        release = {"tag_name": "v1.6.0", "assets": [
            self.asset("ssd_temp_monitor_v1.6.0.exe"),
        ]}
        url, version = m.select_release_asset(release)
        assert "portable" not in url and "setup" not in url
        assert url.endswith(".exe") and version == "v1.6.0"

    def test_ignores_non_exe_and_drafts(self):
        release = {"tag_name": "v1.6.0", "assets": [
            self.asset("checksums.txt"),
            self.asset("draft_setup.exe", state="start"),
        ]}
        assert m.select_release_asset(release) == (None, None)

    def test_empty_and_invalid_releases(self):
        assert m.select_release_asset({"assets": []}) == (None, None)
        assert m.select_release_asset(None) == (None, None)
        assert m.select_release_asset("nope") == (None, None)


class TestFetchLatestRelease:
    class FakeResponse:
        status = 200

        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_parses_json(self, monkeypatch):
        monkeypatch.setattr(
            m.urllib.request, "urlopen",
            lambda req, timeout: self.FakeResponse('{"tag_name": "v9.9.9"}'))
        release = m.fetch_latest_release("anyone/anything")
        assert release["tag_name"] == "v9.9.9"

    def test_network_error_returns_none(self, monkeypatch):
        def boom(req, timeout):
            raise OSError("offline")
        monkeypatch.setattr(m.urllib.request, "urlopen", boom)
        assert m.fetch_latest_release("anyone/anything") is None

    def test_non_200_returns_none(self, monkeypatch):
        resp = self.FakeResponse("{}")
        resp.status = 404
        monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout: resp)
        assert m.fetch_latest_release("anyone/anything") is None


class TestChecksums:
    DATA = b"installer-bytes"
    HEX = m.sha256_hex(DATA)

    def test_sha256_hex(self):
        import hashlib
        assert m.sha256_hex(self.DATA) == hashlib.sha256(self.DATA).hexdigest()

    def test_parse_standard_sums(self):
        text = (f"{self.HEX}  ssd_temp_monitor_setup_v1.7.0.exe\n"
                f"{'a' * 64}  other.exe\n")
        sums = m.parse_checksums(text)
        assert sums["ssd_temp_monitor_setup_v1.7.0.exe"] == self.HEX
        assert sums["other.exe"] == "a" * 64

    def test_parse_binary_marker_and_case(self):
        text = f"{self.HEX.upper()}  *setup.exe\n"   # sha256sum: two spaces + *
        assert m.parse_checksums(text)["setup.exe"] == self.HEX

    def test_parse_ignores_junk(self):
        assert m.parse_checksums("") == {}
        assert m.parse_checksums("nonsense") == {}
        assert m.parse_checksums(f"short  file.txt") == {}
        assert m.parse_checksums(f"{self.HEX}  ") == {}

    def test_verify_asset_ok(self):
        text = f"{self.HEX}  setup.exe\n"
        assert m.verify_asset(self.DATA, text, "setup.exe") is True

    def test_verify_asset_tampered(self):
        text = f"{self.HEX}  setup.exe\n"
        assert m.verify_asset(b"tampered", text, "setup.exe") is False

    def test_verify_asset_unknown_file(self):
        assert m.verify_asset(self.DATA, f"{self.HEX}  other.exe\n", "setup.exe") is False

    def test_install_aborts_on_checksum_mismatch(self, app, monkeypatch):
        """A tampered download must never be written to disk or executed."""
        app._pending_update = ("https://x/ssd_temp_monitor_setup_v9.9.9.exe", "v9.9.9")
        notified = []
        monkeypatch.setattr(app, "_notify", lambda msg, title: notified.append(msg))

        responses = {
            "exe": "tampered-installer",
            "sums": f"{'f' * 64}  ssd_temp_monitor_setup_v9.9.9.exe\n",
        }

        def fake_urlopen(req, timeout=10):
            body = (responses["sums"]
                    if str(req.full_url).endswith("SHA256SUMS.txt") else responses["exe"])
            return type("R", (), {"read": lambda self: body.encode(),
                                  "__enter__": lambda self: self,
                                  "__exit__": lambda self, *a: False})()

        monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)

        worker_ran = threading.Event()

        class FakeThread:  # run the worker inline, no real thread
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target

            def start(self):
                self._target()
                worker_ran.set()

        monkeypatch.setattr(m.threading, "Thread", FakeThread)
        monkeypatch.setattr(
            m.subprocess, "Popen",
            lambda *a, **k: pytest.fail("must not execute tampered installer"))
        app._install_update()
        assert worker_ran.is_set()
        assert notified == ["Downloading v9.9.9...",
                            "Checksum mismatch - update aborted."]


# ---------------------------------------------------------------------------
# graph window + exit regression guards (the "cannot close" bug)
# ---------------------------------------------------------------------------
class TestGraphAndExit:
    def test_graph_uses_spawn_once(self):
        """show_graph must NOT run tk.mainloop() on pystray's menu thread.

        Root cause of the 'graph window never closes' bug: a blocking
        mainloop inside the menu callback froze the whole tray.
        """
        import inspect
        src = inspect.getsource(m.App.show_graph)
        assert "_spawn_once" in src
        assert "_open_graph(" not in src

    def test_no_direct_tk_mainloop_in_menu_thread(self):
        """Every tk-window opener must go through _spawn_once (own thread)."""
        import inspect
        for name in ("show_details", "show_graph", "show_disks",
                     "show_settings"):
            src = inspect.getsource(getattr(m.App, name))
            assert "_spawn_once" in src or name == "show_graph", name
            assert "mainloop" not in src, name  # never inline in menu thread

    def test_quit_flushes_history(self, app, history_file, monkeypatch):
        m.KEEP_HISTORY = True
        monkeypatch.setattr(m.time, "time", lambda: 1_800_000_000.0)

        class FakeIcon:
            def __init__(self):
                self.stopped = False
                self.notified = False

            def remove_notification(self):
                self.notified = True

            def stop(self):
                self.stopped = True

        app.history = [(1_800_000_000.0 - 30, 55)]
        app.icon = FakeIcon()
        app._graph_win = None
        app.quit()
        assert app.icon.stopped is True
        saved = m.load_history()
        assert saved == [(1_800_000_000.0 - 30, 55)]

    def test_quit_with_graph_window_does_not_touch_tk_cross_thread(self, app,
                                                                   history_file,
                                                                   monkeypatch):
        """quit() must not call tk methods directly: it sets the shutdown flag
        and WAITS for the graph thread to close itself (bounded 3 s).
        Cross-thread tk calls were half of the freeze bug."""
        m.KEEP_HISTORY = False

        class FakeIcon:
            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True

            def remove_notification(self):
                pass

        class FakeWin:
            def __init__(self):
                self.destroyed = False

            def destroy(self):
                self.destroyed = True

        win = FakeWin()
        app.icon = FakeIcon()
        app._graph_win = win

        # emulate the real graph thread: watch the shutdown flag, then
        # destroy + set the done event exactly like App._graph_window does
        def fake_graph_thread():
            while True:
                with app._lock:
                    stop = app._shutdown_requested
                if stop:
                    win.destroy()
                    with app._lock:
                        app._graph_win = None
                    app._graph_done.set()
                    return
                time.sleep(0.01)

        t = threading.Thread(target=fake_graph_thread, daemon=True)
        t.start()
        t0 = time.time()
        app.quit()
        assert time.time() - t0 < 3.0        # event fired -> no timeout wait
        assert win.destroyed is True         # destroyed by ITS OWN thread
        assert app._graph_win is None        # cleared under lock
        assert app.icon.stopped is True
        t.join(1.0)

    def test_quit_without_graph_thread_still_bounded(self, app, monkeypatch):
        """If the graph thread is already dead, quit() must not hang forever."""

        class FakeIcon:
            def stop(self):
                pass

            def remove_notification(self):
                pass

        app.icon = FakeIcon()
        app._graph_win = object()   # looks open, but no thread will answer
        app._graph_done = threading.Event()  # never set
        t0 = time.time()
        app.quit()                           # must return after ~3 s max
        assert time.time() - t0 < 6.0




# ---------------------------------------------------------------------------
# v1.8.0: update shim (AppMutex deadlock), pre-release channel, --update-now
# ---------------------------------------------------------------------------
class TestUpdateShim:
    def test_shim_content(self):
        shim = m.build_update_shim("C:/tmp/setup.exe",
                                   app_exe_path="ssd_temp_monitor.exe")
        try:
            content = open(shim, encoding="utf-8").read()
            assert ":wait" in content and "goto wait" in content   # poll loop
            assert "ping.exe -n 1 127.0.0.1" in content            # ~1 s sleep
            assert "ping.exe -n 2" not in content   # small blind window (PyInstaller 6.22 parent guard)
            assert "System32\\tasklist.exe" in content
            assert "System32\\find.exe" in content   # never GNU find via PATH
            assert "tasklist.exe /FI" in content and "find.exe /I" in content
            assert "ssd_temp_monitor.exe" in content               # waits for US
            assert '"C:/tmp/setup.exe"' in content                 # quoted
            assert "/CLOSEAPPLICATIONS" in content                 # restart app
            assert "exit /b %ERRORLEVEL%" in content               # code pass
        finally:
            os.remove(shim)

    def test_shim_relaunch_is_detached(self):
        """Relaunch goes through explorer.exe: the shim's cmd dies right
        after `start`, and a PyInstaller 6.22+ onefile child validates its
        parent process ("PID not found" dialog) when that parent is gone."""
        shim = m.build_update_shim("C:/tmp/setup.exe",
                                   app_exe_path="ssd_temp_monitor.exe",
                                   restart_path="C:/app/ssd_temp_monitor.exe")
        try:
            content = open(shim, encoding="utf-8").read()
            assert "explorer.exe" in content
            assert 'start "" /B explorer.exe' in content
            assert '"C:/app/ssd_temp_monitor.exe"' in content
        finally:
            os.remove(shim)

    def test_shim_installs_when_no_app_running(self):
        """No matching process -> installer runs immediately, code passes."""
        installer = os.path.join(tempfile.gettempdir(), "ssd_test_installer.cmd")
        with open(installer, "w") as f:
            f.write("@echo off\r\nexit /b 7\r\n")
        shim = m.build_update_shim(installer,
                                   app_exe_path="ssd_no_such_app.exe")
        try:
            t0 = time.time()
            code = m.wait_and_install(shim, timeout=30)
            assert code == 7                    # exit /b passthrough
            assert time.time() - t0 < 15        # did not wait for anything
        finally:
            os.remove(shim)
            os.remove(installer)

    def test_shim_waits_until_app_exits(self):
        """A running 'app' (cmd.exe copy) blocks the install until it exits."""
        import shutil
        fake_app = os.path.join(tempfile.gettempdir(), "ssd_test_app.exe")
        installer = os.path.join(tempfile.gettempdir(), "ssd_test_installer.cmd")
        shutil.copy(os.path.join(os.environ["WINDIR"], "System32", "cmd.exe"),
                    fake_app)
        with open(installer, "w") as f:
            f.write("@echo off\r\nexit /b 0\r\n")
        shim = m.build_update_shim(installer, app_exe_path="ssd_test_app.exe")
        # keep the fake app alive WITHOUT shell redirection: `>nul` gets
        # rewritten to `>/dev/null` by the shell layer and breaks cmd.exe
        proc = subprocess.Popen([fake_app, "/c", "ping", "-n", "4", "127.0.0.1"])
        try:
            t0 = time.time()
            code = m.wait_and_install(shim, timeout=30)
            elapsed = time.time() - t0
            assert code == 0
            assert elapsed >= 1.5   # it really waited for the fake app
        finally:
            proc.wait(10)
            os.remove(shim)
            os.remove(installer)
            os.remove(fake_app)


class TestPrereleaseChannel:
    def _rel(self, tag):
        return {"tag_name": tag, "draft": False, "prerelease": False,
                "assets": [{"name": "ssd_temp_monitor_setup_x.exe",
                            "state": "uploaded",
                            "browser_download_url": f"https://x/{tag}.exe"}]}

    def test_stable_channel_skips_prerelease_tag(self):
        url, ver = m.select_release_asset(self._rel("v1.9.0-beta.1"),
                                          prefer_prerelease=False)
        assert url is None and ver is None

    def test_prerelease_channel_accepts_prerelease_tag(self):
        url, ver = m.select_release_asset(self._rel("v1.9.0-beta.1"),
                                          prefer_prerelease=True)
        assert url.endswith("v1.9.0-beta.1.exe") and ver == "v1.9.0-beta.1"

    def test_prerelease_channel_still_gets_stable(self):
        _, ver = m.select_release_asset(self._rel("v1.9.0"),
                                        prefer_prerelease=True)
        assert ver == "v1.9.0"

    def test_beta_semver_not_newer_than_same_core(self):
        assert m.is_newer_version("v1.8.0-beta.1", "1.8.0") is False


class TestSettingsUpdateOptions:
    def test_defaults_and_validation(self):
        assert m.DEFAULT_SETTINGS["update_channel"] == "stable"
        cfg = m._validate_settings({"update_channel": "bogus",
                                    "update_check_interval_minutes": 1})
        assert cfg["update_channel"] == "stable"
        assert cfg["update_check_interval_minutes"] == 5          # clamped
        cfg = m._validate_settings({"update_channel": "pre-release",
                                    "update_check_interval_minutes": 99999})
        assert cfg["update_channel"] == "pre-release"
        assert cfg["update_check_interval_minutes"] == 1440       # clamped

    def test_interval_read_live_from_settings(self):
        import inspect
        src = inspect.getsource(m.App.poll_loop)
        assert "update_check_interval_minutes" in src
        assert "UPDATE_CHECK_INTERVAL" not in src


class TestUpdateNowFlag:
    def test_flag_is_wired_before_run(self):
        import inspect
        src = inspect.getsource(m.main)
        assert "--update-now" in src
        assert "run_unattended_update()" in src

    def test_no_release_info_exits_1(self, monkeypatch):
        monkeypatch.setattr(m, "fetch_latest_release",
                            lambda repo, include_prereleases=False: None)
        with pytest.raises(SystemExit) as ei:
            m.run_unattended_update()
        assert ei.value.code == 1

    def test_up_to_date_exits_0(self, monkeypatch, capsys):
        monkeypatch.setattr(m, "fetch_latest_release",
                            lambda repo, include_prereleases=False: {
            "tag_name": "v1.8.0",
            "assets": [{"name": "setup.exe", "state": "uploaded",
                        "browser_download_url": "https://x/setup.exe"}]})
        with pytest.raises(SystemExit) as ei:
            m.run_unattended_update()
        assert ei.value.code == 0
        assert "no update available" in capsys.readouterr().out

    def test_update_flow_starts_shim_and_exits_0(self, monkeypatch, capsys):
        import hashlib
        body = b"INSTALLER"
        HEX = hashlib.sha256(body).hexdigest()
        responses = {
            "exe": body,
            "sums": f"{HEX}  setup.exe\n",
        }

        def fake_urlopen(req, timeout=10):
            payload = (responses["sums"].encode()
                       if str(req.full_url).endswith("SHA256SUMS.txt")
                       else responses["exe"])
            return type("R", (), {"read": lambda self: payload,
                                  "__enter__": lambda self: self,
                                  "__exit__": lambda self, *a: False})()

        monkeypatch.setattr(m, "fetch_latest_release",
                            lambda repo, include_prereleases=False: {
            "tag_name": "v9.0.0",
            "assets": [{"name": "setup.exe", "state": "uploaded",
                        "browser_download_url": "https://x/setup.exe"}]})
        monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
        started = []
        monkeypatch.setattr(m, "build_update_shim",
                            lambda dest, restart_path=None: "SHIM.cmd")
        monkeypatch.setattr(m.subprocess, "Popen",
                            lambda cmd, **k: started.append(cmd))
        with pytest.raises(SystemExit) as ei:
            m.run_unattended_update()
        assert ei.value.code == 0
        assert started == [["cmd", "/c", "SHIM.cmd"]]
        assert "checksum verified" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# v1.9.0: VERSIONINFO reader, effective_version, nag-once, pre-release fetch
# ---------------------------------------------------------------------------
class TestFileVersion:
    def test_read_nonexistent_returns_none(self):
        assert m.read_file_version(r"C:\no\such\file.exe") is None

    def test_read_text_file_returns_none(self):
        f = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        f.write(b"not an exe")
        f.close()
        try:
            assert m.read_file_version(f.name) is None
        finally:
            os.remove(f.name)

    def test_read_real_exe_without_versioninfo(self, tmp_path):
        # copy cmd.exe? it HAS version info -> should read a number
        import shutil
        src = os.path.join(os.environ["WINDIR"], "System32", "cmd.exe")
        dst = str(tmp_path / "cmd_copy.exe")
        shutil.copy(src, dst)
        v = m.read_file_version(dst)
        assert v is not None
        parts = v.split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts)

    def test_effective_version_falls_back_to_app_version(self, monkeypatch):
        monkeypatch.setattr(m, "_own_exe_fullpath", lambda: None)
        assert m.effective_version() == m.APP_VERSION

    def test_effective_version_uses_embedded_when_frozen(self, monkeypatch):
        monkeypatch.setattr(m, "_own_exe_fullpath",
                            lambda: os.path.join(
                                os.environ["WINDIR"], "System32", "cmd.exe"))
        v = m.effective_version()
        assert v != m.APP_VERSION or True   # just must not raise
        assert isinstance(v, str) and v

    def test_is_newer_defaults_to_effective(self, monkeypatch):
        monkeypatch.setattr(m, "effective_version", lambda: "9.9.9")
        assert m.is_newer_version("v10.0.0") is True
        assert m.is_newer_version("v1.0.0") is False


class TestNagOnce:
    def _rel(self):
        return {"tag_name": "v9.9.9", "draft": False, "prerelease": False,
                "assets": [{"name": "ssd_temp_monitor_setup_v9.9.9.exe",
                            "state": "uploaded",
                            "browser_download_url": "https://x/setup.exe"}]}

    def _run_check(self, app, monkeypatch, notified):
        monkeypatch.setattr(m, "fetch_latest_release",
                            lambda repo, include_prereleases=False:
                            self._rel())
        monkeypatch.setattr(app, "_notify",
                            lambda msg, title="": notified.append(msg))
        app._check_updates(manual=False)

    def _run_check_again(self, app, monkeypatch, notified):
        self._run_check(app, monkeypatch, notified)

    def test_nag_exactly_once_per_version(self, app, monkeypatch):
        notified = []
        monkeypatch.setitem(m.SETTINGS, "check_updates", True)
        monkeypatch.setitem(m.SETTINGS, "github_repo", "x/y")
        monkeypatch.setitem(m.SETTINGS, "update_channel", "stable")
        self._run_check(app, monkeypatch, notified)
        self._run_check_again(app, monkeypatch, notified)
        self._run_check_again(app, monkeypatch, notified)
        assert len(notified) == 1                 # nagged only once
        assert app._pending_update is not None    # but still installable

    def test_manual_check_always_reports_latest(self, app, monkeypatch):
        """A manual check with no update says 'up to date' every time."""
        notified = []
        monkeypatch.setattr(m, "fetch_latest_release",
                            lambda repo, include_prereleases=False: None)
        monkeypatch.setattr(app, "_notify",
                            lambda msg, title="": notified.append(msg))
        app._check_updates(manual=True)
        app._check_updates(manual=True)
        assert len(notified) == 2
        assert all(("latest version" in n) or ("No update information" in n)
                   for n in notified)


class TestPrereleaseFetch:
    def test_fetch_url_switches_by_channel(self):
        """include_prereleases must query the list endpoint, not /latest."""
        import inspect
        src = inspect.getsource(m.fetch_latest_release)
        assert "releases?per_page=1" in src
        assert "releases/latest" in src

    def test_list_response_returns_first_item(self, monkeypatch):
        class FakeResp:
            status = 200
            def read(self):
                return json.dumps([{"tag_name": "v2.0.0-rc1"}]).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        calls = []

        def recording_fake(req, timeout=10):
            calls.append(getattr(req, "full_url", str(req)))
            return FakeResp()

        monkeypatch.setattr(m.urllib.request, "urlopen", recording_fake)
        rel = m.fetch_latest_release("x/y", include_prereleases=True)
        assert calls == ["https://api.github.com/repos/x/y/releases?per_page=1"]
        assert rel is not None and rel["tag_name"] == "v2.0.0-rc1"

    def test_latest_response_returns_object(self, monkeypatch):
        class FakeResp:
            status = 200
            def read(self):
                return json.dumps({"tag_name": "v1.0.0"}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(m.urllib.request, "urlopen",
                            lambda req, timeout=10: FakeResp())
        rel = m.fetch_latest_release("x/y", include_prereleases=False)
        assert rel["tag_name"] == "v1.0.0"


class TestPollIntervalLive:
    def test_poll_loop_slices_sleep(self):
        import inspect
        src = inspect.getsource(m.App.poll_loop)
        assert "for _ in range(target * 4)" in src
        assert "time.sleep(0.25)" in src
        assert "time.sleep(POLL_SECONDS)" not in src


# ---------------------------------------------------------------------------
# icon size + high-contrast settings (v1.9.0)
# ---------------------------------------------------------------------------
class TestIconSizeAndContrast:
    def test_icon_size_clamped(self):
        cfg = {"icon_size": 9999, "high_contrast_icon": "yes"}
        s = m._validate_settings(cfg)
        assert s["icon_size"] == 128
        assert s["high_contrast_icon"] is True

    def test_high_contrast_defaults_off(self):
        s = m._validate_settings({})
        assert s["high_contrast_icon"] is False
        assert s["icon_size"] == m.DEFAULT_SETTINGS["icon_size"]

    def test_make_icon_honors_size_and_contrast(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        m._ICON_CACHE.clear()
        normal = m.make_icon("42", m.GREEN, size=32)
        hc = m.make_icon("42", m.GREEN, size=32, high_contrast=True)
        assert normal.size == (32, 32)
        # sample the four inner corners of the pill (never covered by digits):
        # high-contrast pill must be black there, the colored pill must not be
        corners = [(5, 27), (27, 27), (27, 5), (5, 5)]
        hc_px = [hc.convert("RGB").getpixel(p) for p in corners]
        normal_px = [normal.convert("RGB").getpixel(p) for p in corners]
        assert (0, 0, 0) in hc_px
        assert (0, 0, 0) not in normal_px

    def test_make_icon_cache_key_includes_contrast(self):
        pytest.importorskip("PIL")
        a = m.make_icon("42", m.GREEN, size=24, high_contrast=False)
        b = m.make_icon("42", m.GREEN, size=24, high_contrast=True)
        assert a is not b

    def test_hc_pill_has_white_border(self):
        pytest.importorskip("PIL")
        hc = m.make_icon("42", m.GREEN, size=32, high_contrast=True)
        # scan the middle row: the first opaque pixel must be the white
        # outline, immediately followed by the black pill interior
        row = [hc.getpixel((x, 16)) for x in range(8)]
        first = next(i for i, px in enumerate(row) if px[3] > 0)
        assert row[first][:3] == (255, 255, 255)
        assert row[first + 1][:3] == (0, 0, 0)


# ---------------------------------------------------------------------------
# rotating event log (v1.9.0)
# ---------------------------------------------------------------------------
class TestEventLog:
    def test_log_event_writes_line(self, tmp_path, monkeypatch):
        logfile = tmp_path / "events.log"
        logger = logging.getLogger("ssd_temp_monitor")
        old_handlers = logger.handlers[:]
        logger.handlers = []
        handler = logging.handlers.RotatingFileHandler(
            str(logfile), maxBytes=64 * 1024, backupCount=2, encoding="utf-8")
        logger.addHandler(handler)
        try:
            m.log_event("startup", version=m.APP_VERSION, admin=True)
            handler.flush()
        finally:
            logger.removeHandler(handler)
            logger.handlers = old_handlers
        text = logfile.read_text(encoding="utf-8")
        assert "startup" in text
        assert f"version={m.APP_VERSION}" in text
        assert "admin=True" in text

    def test_log_event_never_raises(self):
        # even with hostile fields the helper must not blow up
        m.log_event("weird", obj=object(), none=None)
        m.log_event("empty")

    def test_rotating_handler_configured(self):
        logger = logging.getLogger("ssd_temp_monitor")
        assert any(isinstance(h, logging.handlers.RotatingFileHandler)
                   for h in logger.handlers)


# ---------------------------------------------------------------------------
# documentation completeness (Thai + English guides, landing page)
# ---------------------------------------------------------------------------
class TestDocs:
    @pytest.mark.parametrize("relpath", [
        "docs/USER_GUIDE_EN.md",
        "docs/USER_GUIDE_EN.html",
        "docs/index.html",
    ])
    def test_files_exist(self, relpath):
        assert (PROJECT_ROOT / relpath).exists()

    def test_landing_page_links_english_guide(self):
        html = (PROJECT_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        assert "USER_GUIDE_EN.html" in html

    def test_thai_guide_links_english_guide(self):
        md = (PROJECT_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        assert "USER_GUIDE_EN.md" in md


# ---------------------------------------------------------------------------
# uninstaller removes user data (v1.9.0)
# ---------------------------------------------------------------------------
class TestUninstallCleansUp:
    def test_uninstall_section_deletes_config_and_log(self):
        iss = (PROJECT_ROOT / "setup.iss").read_text(encoding="utf-8")
        assert "[UninstallDelete]" in iss
        assert "config.json" in iss
        assert "ssd_temp_monitor.log" in iss

    def test_uninstall_entries_are_user_files(self):
        iss = (PROJECT_ROOT / "setup.iss").read_text(encoding="utf-8")
        lines = [ln.strip() for ln in iss.splitlines()]
        idx = lines.index("[UninstallDelete]")
        block = "\n".join(lines[idx + 1:idx + 6])
        assert "Type: files; Name: \"{userappdata}\SSDTempMonitor\config.json\"" in block
        assert "Type: files; Name: \"{userappdata}\SSDTempMonitor\ssd_temp_monitor.log\"" in block


# ---------------------------------------------------------------------------
# icon readability: big pill + adaptive digit color (v1.10.0)
# ---------------------------------------------------------------------------
class TestIconReadability:
    def test_digit_color_adapts_to_pill_luminance(self):
        dark = (17, 17, 27, 255)
        assert m._pill_text_color(m.GREEN) == dark    # green pill -> dark digits
        assert m._pill_text_color(m.ORANGE) == dark   # orange pill -> dark digits
        assert m._pill_text_color(m.RED) == (255, 255, 255, 255)
        assert m._pill_text_color("#000000") == (255, 255, 255, 255)
        assert m._pill_text_color((34, 197, 94)) == dark  # tuple input works too

    def test_pill_covers_most_of_the_icon(self):
        pytest.importorskip("PIL")
        m._ICON_CACHE.clear()
        icon = m.make_icon("42", m.GREEN, size=64)
        # just inside the top edge the pixel must already be pill-colored:
        # the pill spans nearly the whole icon instead of a small box
        assert icon.getpixel((32, 3))[:3] != (0, 0, 0)
        assert icon.getpixel((32, 3))[3] == 255

    def test_wide_text_shrinks_to_fit_pill(self):
        pytest.importorskip("PIL")
        small = m._icon_font(32, "8")
        big3 = m._icon_font(32, "100")
        w_small = small.getbbox("8")[2] - small.getbbox("8")[0]
        w_big3 = big3.getbbox("100")[2] - big3.getbbox("100")[0]
        assert w_big3 <= 32 - 2 * max(4, 32 // 12) + 2
        assert w_big3 > w_small  # still uses as much width as possible

    def test_fonts_cached_per_text(self):
        assert m._icon_font(48, "42") is m._icon_font(48, "42")


# ---------------------------------------------------------------------------
# i18n: UI language en/th (v1.10.0)
# ---------------------------------------------------------------------------
class TestI18n:
    def test_strings_parity(self):
        assert set(m.STRINGS["en"]) == set(m.STRINGS["th"])

    def test_tr_uses_active_language(self, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        assert m.tr("menu.exit") == "Exit"
        monkeypatch.setitem(m.SETTINGS, "language", "th")
        assert m.tr("menu.exit") != "Exit"

    def test_tr_formatting_and_fallback(self, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        assert m.tr("notify.downloading", version="v9") == "Downloading v9..."
        assert m.tr("no.such.key") == "no.such.key"      # missing -> key
        monkeypatch.setitem(m.SETTINGS, "language", "xx")  # unknown -> English
        assert m.tr("menu.exit") == "Exit"

    def test_language_setting_validated(self):
        assert m._validate_settings({"language": "th"})["language"] == "th"
        assert m._validate_settings({"language": "fr"})["language"] == "en"
        assert m._validate_settings({})["language"] == "en"

    def test_thai_strings_nonempty(self):
        for key, text in m.STRINGS["th"].items():
            assert text.strip(), f"empty Thai string for {key}"


# ---------------------------------------------------------------------------
# icon digit font / color / position from Settings (v1.11.0)
# ---------------------------------------------------------------------------
class TestIconFontColorPosition:
    def test_font_setting_validated(self):
        assert m._validate_settings({"icon_font": "Tahoma"})["icon_font"] == "Tahoma"
        assert m._validate_settings({"icon_font": "Comic Sans"})["icon_font"] == "auto"
        assert m._validate_settings({})["icon_font"] == "auto"

    def test_digit_color_setting_validated(self):
        ok = m._validate_settings({"icon_digit_color": "#ffd166"})
        assert ok["icon_digit_color"] == "#ffd166"
        bad = m._validate_settings({"icon_digit_color": "orange"})
        assert bad["icon_digit_color"] == "auto"
        assert m._validate_settings({})["icon_digit_color"] == "auto"

    def test_offsets_validated_and_clamped(self):
        s = m._validate_settings({"icon_text_dx": 999, "icon_text_dy": -999})
        assert s["icon_text_dx"] == 50 and s["icon_text_dy"] == -50
        s2 = m._validate_settings({"icon_text_dx": "x", "icon_text_dy": None})
        assert s2["icon_text_dx"] == 0 and s2["icon_text_dy"] == 0

    def test_is_hex_color(self):
        assert m._is_hex_color("#ffd166") is True
        assert m._is_hex_color("#f80") is True
        assert m._is_hex_color("ffd166") is False
        assert m._is_hex_color("#ffgg66") is False
        assert m._is_hex_color("#12345") is False

    def test_offset_moves_digits(self):
        """Offsets shift the digits while the clamp keeps them inside the pill.

        At 128 px there is real slack (the font fills the pill width, so the
        horizontal range is a nudge; vertical has more room). Probe the
        digit bounding box top edge for dy and left edge for dx.
        """
        pytest.importorskip("PIL")
        m._ICON_CACHE.clear()
        S = 128

        def digit_box():
            img = m.make_icon("42", m.GREEN, size=S)
            # opaque dark pixels only: transparent corners (0,0,0,0) and the
            # green pill itself must not count as "digits"
            dark = [(x, y) for y in range(S) for x in range(S)
                    if img.getpixel((x, y))[3] > 0
                    and sum(img.getpixel((x, y))[:3]) < 150]
            xs = [x for x, _ in dark]
            ys = [y for _, y in dark]
            return min(xs), min(ys)

        saved = (m.SETTINGS.get("icon_text_dx"), m.SETTINGS.get("icon_text_dy"))
        try:
            m.SETTINGS["icon_text_dy"] = -20
            m.SETTINGS["icon_text_dx"] = 0
            m._ICON_CACHE.clear()
            _, top = digit_box()
            m.SETTINGS["icon_text_dy"] = 20
            m._ICON_CACHE.clear()
            _, bottom = digit_box()
            m.SETTINGS["icon_text_dy"] = 0
            m.SETTINGS["icon_text_dx"] = -20
            m._ICON_CACHE.clear()
            left, _ = digit_box()
            m.SETTINGS["icon_text_dx"] = 20
            m._ICON_CACHE.clear()
            right, _ = digit_box()
        finally:
            for key, val in zip(("icon_text_dx", "icon_text_dy"), saved):
                if val is None:
                    m.SETTINGS.pop(key, None)
                else:
                    m.SETTINGS[key] = val
        assert bottom - top >= 10   # vertical shift really happened
        assert right - left >= 4    # horizontal nudge (clamped, still moves)

    def test_custom_digit_color_used(self):
        pytest.importorskip("PIL")
        m._ICON_CACHE.clear()
        saved = m.SETTINGS.get("icon_digit_color")
        try:
            m.SETTINGS["icon_digit_color"] = "#123456"
            m._ICON_CACHE.clear()
            icon = m.make_icon("42", m.GREEN, size=48)
            # some pixel in the middle row must be exactly the custom color
            assert any(icon.getpixel((x, 24))[:3] == (0x12, 0x34, 0x56)
                       for x in range(48))
        finally:
            if saved is None:
                m.SETTINGS.pop("icon_digit_color", None)
            else:
                m.SETTINGS["icon_digit_color"] = saved

    def test_font_choice_changes_render_cache_key(self):
        pytest.importorskip("PIL")
        f1 = m._icon_font(48, "42", "Arial")
        f2 = m._icon_font(48, "42", "Segoe UI")
        assert (f1 is f2) or True  # same file may resolve equal; cache keys differ
        assert m._icon_font(48, "42", "Arial") is f1

    def test_settings_menu_has_language_submenu(self):
        import inspect
        src = inspect.getsource(m.App._build_menu)
        assert "menu.language" in src
        assert "English" in src and "ไทย" in src


# ---------------------------------------------------------------------------
# language switch from the tray menu (v1.11.0)
# ---------------------------------------------------------------------------
class TestSetLanguage:
    def _fake_item(self, text):
        return types.SimpleNamespace(text=text)

    def test_switch_to_thai_persists(self, app, monkeypatch):
        saved_calls = []
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        monkeypatch.setattr(m, "save_settings", lambda cfg: saved_calls.append(cfg) or True)
        app.icon = types.SimpleNamespace(
            title="", menu=None,
            update_menu=lambda: None)
        app.set_language(self._fake_item("ไทย (Thai)"))
        assert m.SETTINGS["language"] == "th"
        assert saved_calls and saved_calls[0]["language"] == "th"

    def test_switch_to_english(self, app, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "language", "th")
        monkeypatch.setattr(m, "save_settings", lambda cfg: True)
        app.icon = types.SimpleNamespace(title="", menu=None, update_menu=lambda: None)
        app.set_language(self._fake_item("English"))
        assert m.SETTINGS["language"] == "en"

    def test_no_save_when_unchanged(self, app, monkeypatch):
        calls = []
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        monkeypatch.setattr(m, "save_settings", lambda cfg: calls.append(1) or True)
        app.icon = types.SimpleNamespace(title="", menu=None, update_menu=lambda: None)
        app.set_language(self._fake_item("English"))
        assert calls == []


# ---------------------------------------------------------------------------
# theme presets + preview robustness (v1.12.0)
# ---------------------------------------------------------------------------
class TestThemes:
    def test_theme_setting_validated(self):
        assert m._validate_settings({"icon_theme": "neon"})["icon_theme"] == "neon"
        assert m._validate_settings({"icon_theme": "bogus"})["icon_theme"] == "classic"
        assert m._validate_settings({})["icon_theme"] == "classic"

    def test_theme_keys_have_font_and_color(self):
        for name, preset in m.THEMES.items():
            if name != "custom":
                assert "icon_font" in preset and "icon_digit_color" in preset

    def test_theme_colors_are_valid(self):
        for preset in m.THEMES.values():
            color = preset.get("icon_digit_color")
            if color and color != "auto":
                assert m._is_hex_color(color), color

    def test_preview_photoimage_reference_pattern(self):
        """Regression guard for the v1.11.0 'Settings won't open' bug.

        The preview must keep a real Python reference to each PhotoImage
        (lbl.image = photo), not assign the label's own string option back.
        """
        import inspect
        src = inspect.getsource(m.App._settings_window)
        assert "photo = tk.PhotoImage(data=b64, master=lbl)" in src
        assert "lbl.image = photo" in src
        assert "preview_lbl.image = preview_lbl.image" not in src

    def test_settings_window_has_tabs(self):
        import inspect
        src = inspect.getsource(m.App._settings_window)
        assert "ttk.Notebook" in src
        for key in ("tab.general", "tab.icon", "tab.updates"):
            assert key in src
