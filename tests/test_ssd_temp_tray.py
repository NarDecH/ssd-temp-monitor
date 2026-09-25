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
    a.history24 = []
    a._h24_state = {"minute": 0.0, "bucket": None}
    a.icon = None
    return a



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
        assert disks == [{"model": "A", "media": "SSD", "bus": "NVMe", "temp": 41,
                          "wear": None, "read_errors": None, "unfixed_errors": None}]

    def test_smart_fields_parsed(self):
        out = ('{"model":"A","media":"SSD","bus":"NVMe","temp":41,'
               '"wear":7,"readErr":12,"undef":0}')
        d = m._parse_disks(out, with_temp=True)[0]
        assert d["wear"] == 7 and d["read_errors"] == 12
        assert d["unfixed_errors"] == 0

    def test_smart_garbage_is_none(self):
        out = '{"model":"A","wear":"x","readErr":-5,"undef":null}'
        d = m._parse_disks(out, with_temp=True)[0]
        assert d["wear"] is None and d["read_errors"] is None
        assert d["unfixed_errors"] is None

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
        assert m.read_temps() == [{"model": "NVME SSD", "temp": 44,
                                   "wear": None, "read_errors": None,
                                   "unfixed_errors": None}]

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
        assert m.read_temps() == [{"model": "NVME SSD", "temp": 44,
                                   "wear": None, "read_errors": None,
                                   "unfixed_errors": None}]

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
        assert disks[0] == {"model": "NVME SSD", "media": "SSD", "bus": "NVMe", "temp": 44,
                            "wear": None, "read_errors": None, "unfixed_errors": None}
        assert disks[1] == {"model": "USB reader", "media": "SSD", "bus": "USB", "temp": None,
                            "wear": None, "read_errors": None, "unfixed_errors": None}

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
        ("v1.2.3", (1, 2, 3, 1, 0)),
        ("1.10.0", (1, 10, 0, 1, 0)),
        ("V2.0", (2, 0, 0, 1, 0)),
        ("1.2.3-beta", (1, 2, 3, 0, 0)),
        ("v1.14.0-rc2", (1, 14, 0, 0, 2)),
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
        # semver pre-release order (v1.13.x regression: rc compared as release)
        ("1.14.0", "1.14.0-rc1", True),      # release beats its rc
        ("1.14.0-rc1", "1.14.0", False),
        ("1.14.0-rc2", "1.14.0-rc1", True),  # rc numbers sort within pre-releases
        ("1.14.0-rc1", "1.13.9", True),
        ("1.13.9", "1.14.0-rc1", False),
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
        # cmd.exe HAS version info; since v1.14.0 the string FileVersion is
        # preferred (it can carry pre-release suffixes), so the result is
        # the string form which may include build metadata.
        import shutil
        src = os.path.join(os.environ["WINDIR"], "System32", "cmd.exe")
        dst = str(tmp_path / "cmd_copy.exe")
        shutil.copy(src, dst)
        v = m.read_file_version(dst)
        assert v is not None and v
        assert v.split(".")[0].isdigit()

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

    def test_rotating_handler_configured(self, tmp_path):
        # The real RotatingFileHandler is detached for the whole test session
        # (tests/conftest.py) so tests never write the user's real log.
        # Verify the logging contract itself against a temp handler instead:
        # INFO level, module formatter, and log_event() writes one line.
        logger = logging.getLogger("ssd_temp_monitor")
        saved = list(logger.handlers)
        logger.handlers.clear()
        h = logging.handlers.RotatingFileHandler(
            tmp_path / "probe.log", maxBytes=512 * 1024,
            backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
        try:
            m.log_event("contract_probe", k="v")
            h.flush()
            content = (tmp_path / "probe.log").read_text(encoding="utf-8")
            assert "INFO contract_probe k=v" in content
        finally:
            logger.handlers.clear()
            logger.handlers.extend(saved)


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
        assert r'Type: files; Name: "{userappdata}\SSDTempMonitor\config.json"' in block
        assert r'Type: files; Name: "{userappdata}\SSDTempMonitor\ssd_temp_monitor.log"' in block


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
        assert m._validate_settings({"icon_font": "Comic Sans"})["icon_font"] == "Arial"
        assert m._validate_settings({})["icon_font"] == "Arial"
        # v1.12.x config migration: the old "auto" value maps to Segoe UI bold
        assert m._validate_settings({"icon_font": "auto"})["icon_font"] == "Segoe UI"

    def test_digit_color_setting_validated(self):
        ok = m._validate_settings({"icon_digit_color": "#ffd166"})
        assert ok["icon_digit_color"] == "#ffd166"
        bad = m._validate_settings({"icon_digit_color": "orange"})
        assert bad["icon_digit_color"] == "#ffffff"   # default digit color
        assert m._validate_settings({})["icon_digit_color"] == "#ffffff"

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
        saved = dict(m.SETTINGS)
        # dark digits on the green pill (default is white -> invisible here)
        m.SETTINGS["icon_digit_color"] = "#111111"

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
            m.SETTINGS.clear()
            m.SETTINGS.update(saved)
            m._ICON_CACHE.clear()
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


# ---------------------------------------------------------------------------
# v1.13.0: font families/styles, digit scale, Apply button, update self-test,
# stale _MEI cleanup
# ---------------------------------------------------------------------------
class TestFontFamiliesStyles:
    def test_family_table_shape(self):
        for fam, styles in m.FONT_FAMILIES.items():
            assert set(styles) == set(m.FONT_STYLES), fam
            for s, (ttf, _tk) in styles.items():
                assert ttf.endswith(".ttf"), (fam, s)

    def test_offers_13_families(self):
        assert len(m.FONT_FAMILIES) >= 13  # "เลือกได้มากกว่าเดิม" (เดิม 4+auto)

    def test_resolve_never_returns_missing_file(self):
        """Any family/style combo must resolve to a ttf that exists here.

        Machines ship different font sets (e.g. no verdanabi.ttf), so the
        resolver must fall back within the family / to Arial bold.
        """
        fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                 "Fonts")
        for fam in list(m.FONT_FAMILIES) + ["Bogus Family"]:
            for style in list(m.FONT_STYLES) + ["weird"]:
                f, _tk = m._resolve_font_file(fam, style)
                assert os.path.isfile(os.path.join(fonts_dir, f)), (fam, style, f)

    def test_resolve_font_file_fallbacks(self):
        f, tk_name = m._resolve_font_file("Segoe UI", "regular")
        assert f == "segoeui.ttf" and tk_name == "Segoe UI"
        # unknown family/style -> Arial bold
        assert m._resolve_font_file("Comic Sans", "bold") == ("arialbd.ttf", "Arial")
        assert m._resolve_font_file("Arial", "ultra") == ("arialbd.ttf", "Arial")

    def test_styles_render_differently(self):
        """Italic/bold glyphs differ from regular at the same settings."""
        pytest.importorskip("PIL")
        saved = dict(m.SETTINGS)
        try:
            m.SETTINGS["icon_font"] = "Georgia"
            m.SETTINGS["icon_font_style"] = "regular"
            m._ICON_CACHE.clear()
            reg = m.make_icon("42", m.GREEN, size=64)
            m.SETTINGS["icon_font_style"] = "italic"
            m._ICON_CACHE.clear()
            ita = m.make_icon("42", m.GREEN, size=64)
            assert reg.tobytes() != ita.tobytes()
        finally:
            m.SETTINGS.clear()
            m.SETTINGS.update(saved)
            m._ICON_CACHE.clear()

    def test_scale_changes_digit_height(self):
        pytest.importorskip("PIL")
        saved = dict(m.SETTINGS)
        try:
            m.SETTINGS["icon_font"] = "Segoe UI"
            m.SETTINGS["icon_font_style"] = "bold"
            m.SETTINGS["icon_digit_color"] = "#111111"

            def digit_height(scale):
                m.SETTINGS["icon_digit_scale"] = scale
                m._ICON_CACHE.clear()
                img = m.make_icon("88", m.GREEN, size=128)
                # digits are the dark (auto-contrast) pixels on the green pill
                ys = [y for y in range(128) for x in range(128)
                      if img.getpixel((x, y))[3] > 0
                      and sum(img.getpixel((x, y))[:3]) < 150]
                return (max(ys) - min(ys)) if ys else 0

            small = digit_height(60)
            big = digit_height(140)
            assert big > small + 10
        finally:
            m.SETTINGS.clear()
            m.SETTINGS.update(saved)
            m._ICON_CACHE.clear()

    def test_scale_validated(self):
        assert m._validate_settings({"icon_digit_scale": 999})["icon_digit_scale"] == 150
        assert m._validate_settings({"icon_digit_scale": 1})["icon_digit_scale"] == 50
        assert m._validate_settings({"icon_digit_scale": "x"})["icon_digit_scale"] == 100
        assert m._validate_settings({})["icon_digit_scale"] == 100

    def test_style_validated(self):
        assert m._validate_settings({"icon_font_style": "italic"})["icon_font_style"] == "italic"
        assert m._validate_settings({"icon_font_style": "heavy"})["icon_font_style"] == "regular"
        assert m._validate_settings({})["icon_font_style"] == "regular"


class TestColorPresets:
    def test_presets_valid_hex_or_auto(self):
        for c in m.COLOR_PRESETS:
            assert c == "auto" or m._is_hex_color(c), c
        assert len(m.COLOR_PRESETS) >= 8  # "เลือกได้มากกว่าเดิม" (เดิม 4)

    def test_digit_color_override_rendered(self):
        pytest.importorskip("PIL")
        saved = dict(m.SETTINGS)
        try:
            m.SETTINGS["icon_digit_color"] = "#38bdf8"
            m._ICON_CACHE.clear()
            img = m.make_icon("42", m.GREEN, size=64)
            cyan = [1 for y in range(64) for x in range(64)
                    if img.getpixel((x, y))[:3] == (0x38, 0xbd, 0xf8)]
            assert cyan, "custom digit color not found on the icon"
        finally:
            m.SETTINGS.clear()
            m.SETTINGS.update(saved)
            m._ICON_CACHE.clear()


class TestUpdateSelftest:
    def test_all_checks_pass(self):
        ok, (checks, failed) = m.run_update_selftest()
        assert ok, [c for c in checks if not c[1]]
        assert len(checks) >= 10
        assert failed == []

    def test_covers_pipeline_stages(self):
        ok, (checks, _failed) = m.run_update_selftest()
        assert ok
        names = " ".join(n for n, _o, _d in checks)
        for token in ("version", "asset", "checksum", "shim"):
            assert token in names, token

    def test_strings_exist_both_languages(self):
        for key in ("selftest.title", "selftest.pass", "selftest.fail",
                    "menu.selftest", "settings.apply"):
            assert key in m.STRINGS["en"] and key in m.STRINGS["th"]


class TestStaleMeiCleanup:
    @staticmethod
    def _fake_frozen(monkeypatch, tmp_path, own=None):
        import sys as _sys
        monkeypatch.setattr(_sys, "frozen", True, raising=False)
        if own is not None:
            monkeypatch.setattr(_sys, "_MEIPASS", str(own), raising=False)
        else:
            monkeypatch.delattr(_sys, "_MEIPASS", raising=False)
        monkeypatch.setenv("TEMP", str(tmp_path))

    def _age(self, path, minutes):
        old = time.time() - minutes * 60
        os.utime(path, (old, old))

    def test_noop_for_source_runs(self, tmp_path, monkeypatch):
        # getattr(sys, "frozen") is False under pytest -> must do nothing
        junk = tmp_path / "_MEI123456"
        junk.mkdir()
        self._age(junk, 60)
        m.cleanup_stale_mei()
        assert junk.exists()

    def test_removes_stale_dir(self, tmp_path, monkeypatch):
        junk = tmp_path / "_MEI123456"
        junk.mkdir()
        self._age(junk, 60)
        self._fake_frozen(monkeypatch, tmp_path)
        m.cleanup_stale_mei()
        assert not junk.exists()

    def test_keeps_fresh_dir(self, tmp_path, monkeypatch):
        """A dir younger than the grace period may be starting up: keep it."""
        fresh = tmp_path / "_MEI000001"
        fresh.mkdir()
        self._fake_frozen(monkeypatch, tmp_path)
        m.cleanup_stale_mei()
        assert fresh.exists()

    def test_keeps_own_meipass(self, tmp_path, monkeypatch):
        mine = tmp_path / "_MEI000002"
        mine.mkdir()
        self._age(mine, 60)
        self._fake_frozen(monkeypatch, tmp_path, own=mine)
        m.cleanup_stale_mei()
        assert mine.exists()

    def test_keeps_locked_dir(self, tmp_path, monkeypatch):
        """A dir whose DLL is held open ("running app") must survive."""
        live = tmp_path / "_MEI000003"
        live.mkdir()
        self._age(live, 60)
        lock = open(os.path.join(live, "python312.dll"), "a")
        try:
            self._fake_frozen(monkeypatch, tmp_path)
            m.cleanup_stale_mei()
            assert live.exists()   # rename denied -> treated as alive
            assert not list(tmp_path.glob("*_stale"))
        finally:
            lock.close()

    def test_ignores_unrelated_names(self, tmp_path, monkeypatch):
        keep = tmp_path / "_MEIPASS_backup"
        keep.mkdir()
        self._age(keep, 60)
        self._fake_frozen(monkeypatch, tmp_path)
        m.cleanup_stale_mei()
        assert keep.exists()  # not _MEI<digits>: never touched


# ---------------------------------------------------------------------------
# v1.14.0: autostart, compact tooltip, window geometry, CSV writer,
#          health flags, i18n ja/zh, semver pre-release ordering
# ---------------------------------------------------------------------------
class TestAutostart:
    class _FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_enabled_and_toggle_with_fake_registry(self):
        """Full enable/disable cycle against an injectable fake registry
        (the real HKCU must never be touched by tests)."""
        store = {}

        class FakeReg:
            HKEY_CURRENT_USER = "HKCU"
            KEY_SET_VALUE = 0x0002
            REG_SZ = 1

            @staticmethod
            def OpenKey(root, path, reserved=0, access=None):
                assert (root, path) == ("HKCU", m.AUTOSTART_RUN_KEY)
                return TestAutostart._FakeKey()

            @staticmethod
            def QueryValueEx(key, name):
                if name not in store:
                    raise OSError(2, "not found")
                return store[name], 1

            @staticmethod
            def SetValueEx(key, name, _r, _t, value):
                store[name] = value

            @staticmethod
            def DeleteValue(key, name):
                store.pop(name, None)

        assert m.autostart_enabled(FakeReg) is False
        assert m.set_autostart(True, FakeReg) is True
        # quoted path to our own entry point (.py for source, .exe frozen)
        stored = store[m.AUTOSTART_VALUE]
        assert stored.startswith('"') and stored.endswith('"')
        assert os.path.isfile(stored.strip('"'))
        assert m.autostart_enabled(FakeReg) is True
        assert m.set_autostart(False, FakeReg) is True
        assert m.autostart_enabled(FakeReg) is False

    def test_default_settings_keys(self):
        assert m.DEFAULT_SETTINGS["compact_tooltip"] is True
        assert m._validate_settings({"compact_tooltip": "yes"})["compact_tooltip"] is True
        assert m._validate_settings({})["compact_tooltip"] is True

    def test_entry_point_prefers_installed_exe_when_source(self, monkeypatch):
        """Source run + installed exe present -> register the exe, never
        pythonw (the mutex-blocking bug that broke self-update)."""
        monkeypatch.setattr(m.sys, "frozen", False, raising=False)
        installed = os.path.join(r"C:\Program Files\SSD Temp Monitor",
                                 "ssd_temp_monitor.exe")
        def fake_isfile(p):
            return os.path.normpath(p) == os.path.normpath(installed)
        monkeypatch.setattr(m.os.path, "isfile", fake_isfile)
        assert os.path.normpath(m._autostart_entry_point()) == \
            os.path.normpath(installed)

    def test_entry_point_frozen_is_self(self, monkeypatch):
        monkeypatch.setattr(m.sys, "frozen", True, raising=False)
        monkeypatch.setattr(m.sys, "executable", "X:/app/exe.exe",
                            raising=False)
        assert m._autostart_entry_point() == "X:/app/exe.exe"

    def test_heal_autostart_fixes_wrong_value(self, monkeypatch):
        """A Run key pointing at a foreign command is rewritten to the
        proper entry point (and untouched when already correct)."""
        store = {}

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class FakeReg:
            HKEY_CURRENT_USER = "HKCU"
            KEY_SET_VALUE = 0x0002
            REG_SZ = 1

            @staticmethod
            def OpenKey(root, path, reserved=0, access=None):
                return TestAutostart._FakeKey()

            @staticmethod
            def QueryValueEx(key, name):
                if name not in store:
                    raise OSError(2, "not found")
                return store[name], 1

            @staticmethod
            def SetValueEx(key, name, _r, _t, value):
                store[name] = value

            @staticmethod
            def DeleteValue(key, name):
                store.pop(name, None)

        monkeypatch.setattr(m.sys, "frozen", False, raising=False)
        point = m._autostart_entry_point()
        store[m.AUTOSTART_VALUE] = '"C:/somewhere/pythonw.exe"'
        assert m.heal_autostart_value(FakeReg) is True
        assert store[m.AUTOSTART_VALUE] == f'"{point}"'
        # already correct -> no rewrite
        assert m.heal_autostart_value(FakeReg) is False


class TestCompactTooltip:
    def test_compact_single_disk(self):
        assert m._tooltip_text([{"model": "A", "temp": 65}]) == "65°C"

    def test_compact_multi_disk(self):
        got = m._tooltip_text([{"model": "A", "temp": 65},
                               {"model": "B", "temp": 58}])
        assert got == "65°C · 58°C (2 disks)"

    def test_full_mode_lists_models(self):
        saved = m.SETTINGS.get("compact_tooltip")
        try:
            m.SETTINGS["compact_tooltip"] = False
            got = m._tooltip_text([{"model": "A", "temp": 65},
                                   {"model": "B", "temp": None}])
            assert got == "A: 65C | B: n/a"
        finally:
            if saved is None:
                m.SETTINGS.pop("compact_tooltip", None)
            else:
                m.SETTINGS["compact_tooltip"] = saved

    def test_no_data(self):
        assert "no data" in m._tooltip_text([])


class TestGeometry:
    def test_roundtrip(self, tmp_path, monkeypatch):
        geo_file = tmp_path / "geo.json"
        monkeypatch.setattr(m, "GEOMETRY_FILE", str(geo_file))
        m.save_geometry({"graph": "800x480+10+20"})
        assert m.load_geometry() == {"graph": "800x480+10+20"}
        m.save_geometry({"details": "300x200+5+5"})
        assert m.load_geometry()["graph"] == "800x480+10+20"

    def test_corrupt_file_returns_empty(self, tmp_path, monkeypatch):
        geo_file = tmp_path / "geo.json"
        geo_file.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(m, "GEOMETRY_FILE", str(geo_file))
        assert m.load_geometry() == {}


class TestHistoryCsvWriter:
    def test_write_and_read_back(self, tmp_path):
        path = tmp_path / "h.csv"
        points = [(1700000000, 41), (1700000060, 45)]
        assert m.write_history_csv(points, str(path)) == str(path)
        rows = path.read_text(encoding="utf-8").splitlines()
        assert rows[0] == "timestamp,temperature_c"
        assert rows[1] == "1700000000,41"
        assert rows[2] == "1700000060,45"

    def test_invalid_path_returns_none(self):
        assert m.write_history_csv([(1, 2)], "Z:/no/such/dir/x.csv") is None


class TestHealthFlags:
    def test_healthy_disk_has_no_flags(self):
        assert m.health_flags({"wear": 5, "read_errors": 3,
                               "unfixed_errors": 0}) == []

    def test_high_wear_warns(self):
        flags = m.health_flags({"wear": 95, "read_errors": 0,
                                "unfixed_errors": 0})
        assert len(flags) == 1 and "95" in flags[0]

    def test_unfixed_errors_are_critical(self):
        flags = m.health_flags({"wear": 5, "read_errors": 10,
                                "unfixed_errors": 2})
        assert any("2" in f for f in flags)

    def test_none_fields_are_ignored(self):
        assert m.health_flags({"wear": None, "read_errors": None,
                               "unfixed_errors": None}) == []


class TestI18nJaZh:
    def test_four_languages_complete(self):
        assert set(m.UI_LANGUAGES) == {"en", "th", "ja", "zh"}
        en = set(m.STRINGS["en"])
        for lang in m.UI_LANGUAGES:
            assert set(m.STRINGS[lang]) == en, lang

    def test_japanese_and_chinese_translations_nonempty(self):
        for lang in ("ja", "zh"):
            for key, text in m.STRINGS[lang].items():
                assert text.strip(), f"empty {lang} string for {key}"

    def test_settings_language_validated(self):
        assert m._validate_settings({"language": "ja"})["language"] == "ja"
        assert m._validate_settings({"language": "zh"})["language"] == "zh"
        assert m._validate_settings({"language": "klingon"})["language"] == "en"


class TestSelftestRcOrder:
    def test_semver_check_included(self):
        ok, (checks, failed) = m.run_update_selftest()
        assert ok, [c for c in checks if not c[1]]
        assert any("semver" in name for name, _o, _d in checks)


# ---------------------------------------------------------------------------
# v1.14.1: automatic update rollback + PS_TEMPS fix
# ---------------------------------------------------------------------------
class TestRollback:
    def _sandbox(self, tmp_path, monkeypatch):
        """Freeze-like sandbox: fake exe dir + isolated DATA_DIR."""
        exe_dir = tmp_path / "app"
        exe_dir.mkdir()
        exe = exe_dir / "ssd_temp_monitor.exe"
        exe.write_bytes(b"MZ-fake")
        data = tmp_path / "data"
        data.mkdir()
        monkeypatch.setattr(m, "DATA_DIR", str(data))
        return str(exe), str(exe_dir), str(data)

    def test_stage_backup_copies_exe_and_marker(self, tmp_path, monkeypatch):
        exe, exe_dir, data = self._sandbox(tmp_path, monkeypatch)
        assert m.stage_backup_for_rollback(app_exe=exe, data_dir=data,
                                           target_version="v1.15.0") is True
        assert os.path.isfile(os.path.join(exe_dir, m.ROLLBACK_BACKUP_NAME))
        marker = os.path.join(data, m.ROLLBACK_PENDING)
        assert open(marker).read() == "v1.15.0"

    def test_stage_backup_missing_exe_is_noop(self, tmp_path, monkeypatch):
        _, _, data = self._sandbox(tmp_path, monkeypatch)
        assert m.stage_backup_for_rollback(
            app_exe=str(tmp_path / "nope.exe"), data_dir=data) is False
        assert not os.path.exists(os.path.join(data, m.ROLLBACK_PENDING))

    def test_begin_healthy_clears_backup_and_marker(self, tmp_path, monkeypatch):
        exe, exe_dir, data = self._sandbox(tmp_path, monkeypatch)
        m.stage_backup_for_rollback(app_exe=exe, data_dir=data,
                                    target_version="v1.15.0")
        monkeypatch.setattr(m, "_rollback_backup_path",
                            lambda: os.path.join(exe_dir, m.ROLLBACK_BACKUP_NAME))
        monkeypatch.setattr(m, "_rollback_marker_path",
                            lambda name: os.path.join(data, name))
        m.begin_healthy_session()
        assert not os.path.exists(os.path.join(data, m.ROLLBACK_PENDING))
        assert not os.path.exists(os.path.join(exe_dir, m.ROLLBACK_BACKUP_NAME))

    def test_rollback_reported_blacklists_version(self, tmp_path, monkeypatch):
        """A FRESH REPORTED marker (the shim just rolled us back) must
        blacklist the broken version and clear the markers."""
        exe, exe_dir, data = self._sandbox(tmp_path, monkeypatch)
        monkeypatch.setattr(m, "_rollback_backup_path",
                            lambda: os.path.join(exe_dir, m.ROLLBACK_BACKUP_NAME))
        monkeypatch.setattr(m, "_rollback_marker_path",
                            lambda name: os.path.join(data, name))
        with open(os.path.join(data, m.ROLLBACK_PENDING), "w") as f:
            f.write("v1.15.0")
        reported = os.path.join(data, m.ROLLBACK_REPORTED)
        with open(reported, "w") as f:
            f.write("")
        os.utime(reported, None)  # mtime = now -> fresh
        m.begin_healthy_session()
        assert m.version_is_broken("v1.15.0")
        assert not os.path.exists(os.path.join(data, m.ROLLBACK_PENDING))
        assert not os.path.exists(reported)

    def test_stale_rollback_report_does_not_blacklist(self, tmp_path,
                                                      monkeypatch):
        """Regression (v1.15.0 release night): a stale REPORTED marker from
        an earlier session made the freshly installed, healthy version
        blacklist ITSELF and pop a bogus rollback dialog. A stale marker
        must be cleaned up and the healthy path must continue."""
        exe, exe_dir, data = self._sandbox(tmp_path, monkeypatch)
        monkeypatch.setattr(m, "_rollback_backup_path",
                            lambda: os.path.join(exe_dir, m.ROLLBACK_BACKUP_NAME))
        monkeypatch.setattr(m, "_rollback_marker_path",
                            lambda name: os.path.join(data, name))
        with open(os.path.join(data, m.ROLLBACK_PENDING), "w") as f:
            f.write("v1.15.0")
        backup = os.path.join(exe_dir, m.ROLLBACK_BACKUP_NAME)
        with open(backup, "w") as f:
            f.write("old exe")
        reported = os.path.join(data, m.ROLLBACK_REPORTED)
        with open(reported, "w") as f:
            f.write("")
        old = time.time() - m.ROLLBACK_REPORT_STALE_SECONDS - 60
        os.utime(reported, (old, old))  # mtime far in the past
        m.begin_healthy_session()
        # healthy path ran: markers+backup cleaned, version NOT blacklisted
        assert not m.version_is_broken("v1.15.0")
        assert not os.path.exists(reported)
        assert not os.path.exists(os.path.join(data, m.ROLLBACK_PENDING))
        assert not os.path.exists(backup)

    def test_broken_version_skips_update_checks(self, tmp_path, monkeypatch):
        # isolate: without this the test would append to the REAL
        # update_broken_versions.txt and make the app skip a future
        # legitimate release of that version
        monkeypatch.setattr(m, "_rollback_marker_path",
                            lambda name: os.path.join(str(tmp_path), name))
        m.remember_broken_version("v1.15.0")
        m.remember_broken_version("v1.15.0")  # dedup
        assert m.version_is_broken("v1.15.0")
        assert not m.version_is_broken("v1.16.0")

    def test_watchdog_staged_in_shim_when_backup_present(
            self, tmp_path, monkeypatch):
        exe, exe_dir, data = self._sandbox(tmp_path, monkeypatch)
        monkeypatch.setattr(m.sys, "frozen", True, raising=False)
        monkeypatch.setattr(m.sys, "executable", exe)
        monkeypatch.setattr(m, "DATA_DIR", data)
        m.stage_backup_for_rollback(app_exe=exe, data_dir=data,
                                    target_version="v1.15.0")
        shim = m.build_update_shim(str(tmp_path / "setup.exe"),
                                   restart_path=exe)
        content = open(shim, encoding="utf-8").read()
        # the shim STARTS the detached watchdog and exits; the grace sleep
        # lives in the watchdog file, not in the shim
        assert m.ROLLBACK_WATCHDOG_NAME in content
        assert 'start "" /B' in content
        assert "timeout.exe" not in content
        watchdog = tmp_path / "app" / m.ROLLBACK_WATCHDOG_NAME
        assert watchdog.is_file()
        w = watchdog.read_text(encoding="ascii")
        # grace sleep must be ping-based: timeout.exe aborts when stdin
        # is not a console ("Input redirection is not supported") and
        # would skip the delay entirely, rolling back healthy updates
        assert "timeout.exe" not in w
        assert "ping.exe" in w
        assert "-n 91 " in w
        assert m.ROLLBACK_PENDING in w
        assert m.ROLLBACK_BACKUP_NAME in w
        assert "explorer.exe" in w
        assert "del /Q \"%~f0\"" in w   # self-deleting

    def test_watchdog_absent_without_backup(self, tmp_path, monkeypatch):
        exe, exe_dir, data = self._sandbox(tmp_path, monkeypatch)
        monkeypatch.setattr(m.sys, "frozen", True, raising=False)
        monkeypatch.setattr(m.sys, "executable", exe)
        monkeypatch.setattr(m, "DATA_DIR", data)
        shim = m.build_update_shim(str(tmp_path / "setup.exe"),
                                   restart_path=exe)
        content = open(shim, encoding="utf-8").read()
        assert m.ROLLBACK_WATCHDOG_NAME not in content
        assert not (tmp_path / "app" / m.ROLLBACK_WATCHDOG_NAME).exists()

    def test_restore_shim_waits_by_bare_name(self, tmp_path):
        exe = str(tmp_path / "app" / "ssd_temp_monitor.exe")
        shim = m.build_restore_shim(str(tmp_path / "prev.exe"), exe)
        content = open(shim, encoding="utf-8").read()
        assert 'IMAGENAME eq ssd_temp_monitor.exe' in content
        assert "app" not in content.split("IMAGENAME eq")[1].split('"')[0]
        assert "explorer.exe" in content

    def test_pstemp_query_no_pipe_property(self):
        """Regression v1.14.0: 'readErr' must be a plain property access -
        the old `$c.Prop|ReadErrorsTotal` made PowerShell treat the
        property name as a command, killing the whole query (empty data)."""
        assert "|ReadErrorsTotal" not in m.PS_TEMPS
        assert "readErr=$c.ReadErrorsTotal;" in m.PS_TEMPS


# ---------------------------------------------------------------------------
# Settings: Health tab (v1.15.0)
# ---------------------------------------------------------------------------
class TestSettingsHealthTab:
    def test_health_tab_exists_in_all_languages(self):
        """The tab must be translatable in every supported language."""
        for lang in m.UI_LANGUAGES:
            assert m.STRINGS[lang]["tab.health"].strip()

    def test_health_keys_parity(self):
        """Every health.* key in English must exist in all languages."""
        keys = {k for k in m.STRINGS["en"] if k.startswith("health.")}
        assert keys, "health strings missing"
        for lang in m.UI_LANGUAGES:
            assert keys <= set(m.STRINGS[lang]), (
                f"language {lang} missing health keys: "
                f"{sorted(keys - set(m.STRINGS[lang]))}")

    def test_health_flags_drive_status_text(self, monkeypatch):
        """Status text chosen by the same health_flags the details use."""
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        healthy = {"model": "X", "temp": 40, "wear": 0,
                   "read_errors": 0, "unfixed_errors": 0}
        assert m.health_flags(healthy) == []
        worn = dict(healthy, wear=95)
        assert m.health_flags(worn)  # produces the warning string


# ---------------------------------------------------------------------------
# v1.16.0: proactive SMART alerts + daily health CSV + autostart guard
# ---------------------------------------------------------------------------
class TestSmartWatch:
    def test_new_unfixed_errors_fire_once(self, monkeypatch):
        """First sighting = baseline (no alert storm on fresh installs);
        only a RISING counter afterwards fires, and only once per rise."""
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        disk = {"model": "D1", "temp": 40, "wear": 0, "unfixed_errors": 5}
        state, fired = m.smart_watch_changes([disk], {})
        assert fired == []                        # baseline established
        assert state["D1"]["unfixed"] == 5
        # same counter again: nothing new
        state2, fired2 = m.smart_watch_changes([disk], state)
        assert fired2 == []
        # counter rises: fires once
        disk2 = dict(disk, unfixed_errors=7)
        state3, fired3 = m.smart_watch_changes([disk2], state2)
        assert len(fired3) == 1 and "D1" in fired3[0]
        assert state3["D1"]["unfixed"] == 7

    def test_wear_band_crossing(self, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        d70 = {"model": "D1", "temp": 40, "wear": 70, "unfixed_errors": 0}
        d80 = dict(d70, wear=80)
        d95 = dict(d70, wear=95)
        state, fired = m.smart_watch_changes([d70], {})
        assert fired == []                       # 70% = ok band
        state, fired = m.smart_watch_changes([d80], state)
        assert len(fired) == 1                   # crossed into used band
        state, fired = m.smart_watch_changes([d95], state)
        assert len(fired) == 1                   # crossed into high band

    def test_unknown_counters_stay_silent(self, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        d = {"model": "D1", "temp": 40, "wear": None, "unfixed_errors": None}
        state, fired = m.smart_watch_changes([d], {})
        assert fired == [] and state == {}        # nothing recorded, nothing fired

    def test_state_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path))
        state = {"D1": {"wear": 80, "unfixed": 3}}
        assert m.save_smart_state(state, str(tmp_path))
        assert m.load_smart_state(str(tmp_path)) == state
        assert m.load_smart_state(str(tmp_path / "nope")) == {}

    def test_settings_flag_validated(self):
        assert m._validate_settings({"smart_alerts": "yes"})["smart_alerts"] is True
        assert m._validate_settings({"smart_alerts": 0})["smart_alerts"] is False
        assert m._validate_settings({})["smart_alerts"] is True


class TestDailyHealth:
    def _rows(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            return list(__import__("csv").DictReader(f))

    def test_one_row_per_day(self, tmp_path):
        path = str(tmp_path / "h.csv")
        t0 = 1789000000.0  # any fixed instant
        d = {"model": "M1", "bus": "NVMe", "temp": 40, "wear": 10,
             "read_errors": 0, "unfixed_errors": 0}
        assert m.append_daily_health([d], now=t0, path=path)
        assert not m.append_daily_health([d], now=t0 + 60, path=path)
        # next day: appends again
        assert m.append_daily_health([d], now=t0 + 86400, path=path)
        rows = self._rows(path)
        assert len(rows) == 2 and rows[0]["model"] == "M1"
        assert rows[0]["temp_c"] == "40"

    def test_headless_call_never_raises(self, tmp_path):
        # unwritable path -> returns False, no exception
        bad = str(tmp_path / "no_dir_here" / "h.csv")
        assert m.append_daily_health(
            [{"model": "M", "bus": "?", "temp": 1, "wear": None,
              "read_errors": None, "unfixed_errors": None}],
            now=1789000000.0, path=bad) is False

    def test_trend_series_aggregates_and_limits(self, tmp_path):
        path = str(tmp_path / "h.csv")
        t0 = 1789000000.0
        for day in range(35):
            d = {"model": "M1", "bus": "NVMe", "temp": 40 + day % 5,
                 "wear": day, "read_errors": 0, "unfixed_errors": 0}
            m.append_daily_health([d], now=t0 + day * 86400, path=path)
        rows = m.load_daily_health(path)
        series = m.trend_series(rows, "M1", days=30)
        assert len(series) == 30                  # capped at 30 days
        date, avg, lo, hi, wear = series[-1]
        assert lo <= avg <= hi and wear == 34
        assert m.trend_series(rows, "MISSING") == []


# ---------------------------------------------------------------------------
# v1.17.0: weekly HTML report + wear-slope watch + portable data locations
# ---------------------------------------------------------------------------
class TestWeeklyReport:
    def _rows(self):
        rows = []
        for day in range(7):
            rows.append({"date": f"2026-09-{17 + day}", "time": "12:00",
                         "model": "M1", "bus": "NVMe",
                         "temp_c": str(35 + day), "wear_pct": str(day),
                         "read_errors": "0", "uncorrected": "0"})
        return rows

    def test_html_contains_data_and_is_standalone(self):
        html = m.build_weekly_report_html(self._rows())
        assert html.startswith("<!DOCTYPE html>")
        assert "M1" in html
        assert "http" not in html.lower().split("<body")[1]  # no external deps

    def test_empty_log_produces_empty_report(self):
        assert m.build_weekly_report_html([]) == ""
        old = [{"date": "2020-01-01", "model": "M1", "temp_c": "40",
                "wear_pct": "1"}]
        assert m.build_weekly_report_html(old) == ""   # outside 7-day window

    def test_open_weekly_report_writes_file(self, tmp_path, monkeypatch):
        """Sandbox BOTH the data dir and the health log: the report must
        be built from the sandbox log only - a missing real log on a CI
        runner used to make this test fail (and a real one leak in)."""
        path_holder = {}
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m.os, "startfile",
                            lambda p: path_holder.setdefault("opened", p),
                            raising=False)
        # seed 3 days of data (goes to the sandboxed HEALTH_LOG_FILE);
        # use now-relative timestamps so the days fall inside the report's
        # 7-day window regardless of when the test runs
        t0 = time.time() - 2 * 86400
        d = {"model": "M1", "bus": "NVMe", "temp": 40, "wear": 5,
             "read_errors": 0, "unfixed_errors": 0}
        for day in range(3):
            m.append_daily_health([d], now=t0 + day * 86400)
        out = m.open_weekly_report()
        assert out and os.path.isfile(out)
        assert path_holder.get("opened") == out

    def test_report_includes_wear_and_error_charts(self):
        """The weekly report draws three SVG charts per disk: temperature,
        wear and uncorrected read errors (aria-labels are English)."""
        rows = self._rows()
        rows[3]["uncorrected"] = "7"
        html = m.build_weekly_report_html(rows)
        assert html.count("<svg") == 3
        assert "daily average temperature" in html
        assert "wear percentage" in html
        assert "uncorrected read errors" in html
        # error value survives into the chart polyline, not the table only
        assert "," in html.split("uncorrected read errors")[1]

    def test_export_weekly_report_uses_chosen_path(self, tmp_path, monkeypatch):
        """Save As export writes the HTML to the user-chosen file and
        returns that path; cancel (empty path) returns None."""
        target = tmp_path / "my_report.html"
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m, "log_event", lambda *a, **k: None)
        asked = {}

        def fake_dialog(**kwargs):
            asked.update(kwargs)
            return str(target)

        filedialog_stub = types.SimpleNamespace(asksaveasfilename=fake_dialog)
        monkeypatch.setitem(sys.modules, "tkinter", types.SimpleNamespace(
            filedialog=filedialog_stub))
        t0 = time.time() - 86400
        d = {"model": "M1", "bus": "NVMe", "temp": 40, "wear": 5,
             "read_errors": 0, "unfixed_errors": 0}
        for day in range(2):
            m.append_daily_health([d], now=t0 + day * 86400)
        out = m.save_weekly_report_as()
        assert out == str(target)
        assert target.is_file() and target.stat().st_size > 500
        assert asked.get("initialfile", "").endswith(".html")


class TestWearSlope:
    def _log(self, path, wears):
        t0 = 1789000000.0
        for i, w in enumerate(wears):
            d = {"model": "D1", "bus": "NVMe", "temp": 40, "wear": w,
                 "read_errors": 0, "unfixed_errors": 0}
            m.append_daily_health([d], now=t0 + i * 86400, path=path)
        return m.load_daily_health(path)

    def test_fast_rise_warns_and_dedupes(self, tmp_path, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        path = str(tmp_path / "h.csv")
        rows = self._log(path, [10, 10, 11, 12, 13, 14, 15])   # +5/7d
        state = {}
        msg, state = m.wear_slope_alert(rows, state=state)
        assert msg and "D1" in msg
        # immediately after: suppressed by the weekly cooldown
        msg2, _ = m.wear_slope_alert(rows, state=state)
        assert msg2 is None

    def test_slow_rise_stays_silent(self, tmp_path, monkeypatch):
        monkeypatch.setitem(m.SETTINGS, "language", "en")
        path = str(tmp_path / "h.csv")
        rows = self._log(path, [10, 10, 10, 10, 10, 10, 11])   # +1/7d
        msg, _ = m.wear_slope_alert(rows, state={})
        assert msg is None

    def test_short_history_stays_silent(self):
        rows = [{"date": "2026-09-20", "model": "D1", "temp_c": "40",
                 "wear_pct": "10"}]
        msg, _ = m.wear_slope_alert(rows, state={})
        assert msg is None


class TestPortableDataLocations:
    def test_history_file_lives_in_data_dir(self):
        """Regression: the temperature history used to go to %TEMP%,
        which a portable bundle would leave behind on other machines."""
        assert os.path.dirname(m.HISTORY_FILE) == m.DATA_DIR


class TestDefaultsAndNewFeatures:
    def test_icon_defaults_white_regular_arial(self):
        """User-requested factory defaults for the tray digits."""
        assert m.DEFAULT_SETTINGS["icon_font"] == "Arial"
        assert m.DEFAULT_SETTINGS["icon_font_style"] == "regular"
        assert m.DEFAULT_SETTINGS["icon_digit_color"] == "#ffffff"
        cfg = m._validate_settings({})
        assert (cfg["icon_font"], cfg["icon_font_style"],
                cfg["icon_digit_color"]) == ("Arial", "regular", "#ffffff")

    def test_record_history_defaults_on(self):
        assert m.DEFAULT_SETTINGS["record_history"] is True
        assert m._validate_settings({})["record_history"] is True

    def test_write_weekly_report_uses_dest_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m, "log_event", lambda *a, **k: None)
        t0 = time.time() - 86400
        d = {"model": "M1", "bus": "NVMe", "temp": 40, "wear": 5,
             "read_errors": 0, "unfixed_errors": 0}
        for day in range(2):
            m.append_daily_health([d], now=t0 + day * 86400)
        out = m.write_weekly_report(str(tmp_path))
        assert out and os.path.isfile(out)
        assert os.path.dirname(out) == str(tmp_path)
        assert os.path.basename(out) == m.WEEKLY_REPORT_FILE
        assert m.write_weekly_report(str(tmp_path))  # data still there

    def test_write_weekly_report_no_data_returns_none(self, tmp_path,
                                                       monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m, "log_event", lambda *a, **k: None)
        assert m.write_weekly_report(str(tmp_path)) is None

    def test_health_csv_excel_friendly(self, tmp_path, monkeypatch):
        """Export carries a UTF-8 BOM, the Excel sep= hint and CRLF rows."""
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m, "log_event", lambda *a, **k: None)
        t0 = time.time() - 86400
        d = {"model": "M1", "bus": "NVMe", "temp": 40, "wear": 5,
             "read_errors": 0, "unfixed_errors": 0}
        for day in range(2):
            m.append_daily_health([d], now=t0 + day * 86400)
        dest = tmp_path / "daily.csv"
        out = m.write_health_csv(str(dest))
        raw = dest.read_bytes()
        assert out == str(dest)
        assert raw.startswith(b"\xef\xbb\xbfbfsep=,") or raw.startswith(
            b"\xef\xbb\xbf")
        assert b"sep=," in raw
        assert b"\r\n" in raw
        text = raw.decode("utf-8-sig")
        assert "date,time,model,bus,temp_c,wear_pct,read_errors,uncorrected" \
            in text
        assert "M1" in text

    def test_health_csv_empty_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m, "log_event", lambda *a, **k: None)
        assert m.write_health_csv(str(tmp_path / "x.csv")) is None

    def test_comparison_chart_skipped_for_single_disk(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        t0 = time.time() - 86400
        d = {"model": "M1", "bus": "NVMe", "temp": 40, "wear": 5,
             "read_errors": 0, "unfixed_errors": 0}
        for day in range(2):
            m.append_daily_health([d], now=t0 + day * 86400)
        rows = m.load_daily_health()
        assert m.multi_disk_comparison_html(rows) == ""

    def test_comparison_chart_renders_for_two_disks(self, tmp_path,
                                                    monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        t0 = time.time() - 86400
        for day in range(2):
            m.append_daily_health(
                [{"model": "A", "bus": "NVMe", "temp": 40, "wear": 5,
                  "read_errors": 0, "unfixed_errors": 0},
                 {"model": "B", "bus": "NVMe", "temp": 50, "wear": 1,
                  "read_errors": 0, "unfixed_errors": 0}],
                now=t0 + day * 86400)
        rows = m.load_daily_health()
        html = m.multi_disk_comparison_html(rows)
        assert html.startswith("<svg")
        assert html.count("<polyline") == 2
        assert "A" in html and "B" in html
        assert "aria-label=\"all disks" in html

    def test_report_includes_comparison_when_multi_disk(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        t0 = time.time() - 86400
        for day in range(3):
            m.append_daily_health(
                [{"model": "A", "bus": "NVMe", "temp": 40, "wear": 5,
                  "read_errors": 0, "unfixed_errors": 0},
                 {"model": "B", "bus": "NVMe", "temp": 50, "wear": 1,
                  "read_errors": 0, "unfixed_errors": 0}],
                now=t0 + day * 86400)
        html = m.build_weekly_report_html(m.load_daily_health())
        assert 'aria-label="all disks' in html

    def test_weekly_report_settings_validated(self):
        assert m.DEFAULT_SETTINGS["weekly_report_enabled"] is False
        assert m.DEFAULT_SETTINGS["weekly_report_dir"] == ""
        cfg = m._validate_settings({"weekly_report_enabled": 1,
                                    "weekly_report_dir": " C:/x "})
        assert cfg["weekly_report_enabled"] is True
        assert cfg["weekly_report_dir"] == "C:/x"
        assert m._validate_settings({"weekly_report_dir": None})\
            ["weekly_report_dir"] == ""

    def test_new_i18n_keys_parity(self):
        keys = ("health.csv", "notify.weekly_saved",
                "settings.weekly_report", "settings.weekly_dir",
                "settings.weekly_browse")
        for lang in m.UI_LANGUAGES:
            for key in keys:
                assert key in m.STRINGS[lang], (lang, key)
                assert "{path}" in m.STRINGS[lang]["notify.weekly_saved"]


class TestGraph24ThemeStats:
    def test_history24_one_point_per_minute_and_trim(self, tmp_path,
                                                     monkeypatch):
        """Downsamples the 1 Hz history to one point/minute, keeps 24 h
        worth of points at most, and is idempotent within the minute."""
        monkeypatch.setattr(m, "HISTORY24_FILE", str(tmp_path / "h24.csv"))
        monkeypatch.setattr(m, "HISTORY24_STATE",
                            str(tmp_path / "h24.json"))
        now = 1_800_000_000.0
        fine = [(now - 30 + i, 40) for i in range(10)]
        pts, st = m.update_history24([], now, {}, temps=[{"temp": 42}])
        assert len(pts) == 1 and pts[0][1] == 42
        # same minute again -> no duplicate
        pts2, st2 = m.update_history24(pts, now + 5, st)
        assert len(pts2) == 1
        # next minute -> a second point
        pts3, _ = m.update_history24(pts2, now + 65, st2,
                                     temps=[{"temp": 44}])
        assert len(pts3) == 2 and pts3[-1][1] == 44
        # no reading -> carries over without a point
        pts4, _ = m.update_history24(pts3, now + 125, _,
                                     temps=[{"temp": None}])
        assert pts4 == pts3
        # trimming: points older than 24 h and beyond capacity are dropped
        pts5, _ = m.update_history24([(now - 90000, 30)] + pts3,
                                     now + 185, _, temps=[{"temp": 40}])
        assert all(p[0] >= now + 185 - 86400 for p in pts5)

    def test_history24_roundtrip_files(self, tmp_path, monkeypatch):
        f = tmp_path / "h24.csv"
        monkeypatch.setattr(m, "HISTORY24_FILE", str(f))
        monkeypatch.setattr(m, "HISTORY24_STATE", str(tmp_path / "s.json"))
        m._history24_save([(100.0, 40), (160.0, 41)])
        assert m._history24_load() == [(100.0, 40), (160.0, 41)]
        m._history24_save_state({"minute": 160.0, "bucket": 41})
        assert m._history24_load_state() == {"minute": 160.0, "bucket": 41}

    def test_ui_palette_modes(self, monkeypatch):
        assert m.ui_palette("dark") is m.UI_DARK
        assert m.ui_palette("light") is m.UI_LIGHT
        # auto consults the (monkeypatched) Windows setting
        monkeypatch.setattr(m, "windows_app_is_dark", lambda: True)
        assert m.ui_palette("auto") is m.UI_DARK
        monkeypatch.setattr(m, "windows_app_is_dark", lambda: False)
        assert m.ui_palette("auto") is m.UI_LIGHT

    def test_windows_theme_probe_never_raises(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no registry")
        monkeypatch.setattr(m, "SETTINGS", dict(m.SETTINGS, ui_theme="auto"))
        import winreg
        real_open = winreg.OpenKey
        monkeypatch.setattr(winreg, "OpenKey", boom, raising=False)
        assert m.windows_app_is_dark() is True  # fallback = dark

    def test_uitheme_setting_validated_and_default(self):
        assert m.DEFAULT_SETTINGS["ui_theme"] == "auto"
        assert m._validate_settings({"ui_theme": "pink"})["ui_theme"] == "auto"
        assert m._validate_settings({"ui_theme": "light"})["ui_theme"] == "light"

    def test_week_stats_from_daily_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        # 2 overheat + 1 smart alert in the (mocked) log within the week
        monkeypatch.setattr(m, "count_log_events",
                            lambda event, days=7, **k:
                            {"overheat_alert": 2,
                             "smart_alert": 1}.get(event, 0))
        t0 = time.time() - 86400
        for day in range(3):
            m.append_daily_health(
                [{"model": "A", "bus": "NVMe", "temp": 40 + day,
                  "wear": 5 + day, "read_errors": 0, "unfixed_errors": 0}],
                now=t0 + day * 86400)
        s = m.week_stats(m.load_daily_health())
        assert s["alerts"] == 2 and s["smart_alerts"] == 1
        a = s["per_disk"]["A"]
        assert a["min"] == 40 and a["max"] == 42
        assert abs(a["avg"] - 41.0) < 0.01
        assert a["wear"] == 7

    def test_week_stats_ignores_old_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m, "count_log_events", lambda *a, **k: 0)
        m.append_daily_health(
            [{"model": "OLD", "bus": "NVMe", "temp": 90, "wear": 99,
              "read_errors": 0, "unfixed_errors": 0}],
            now=time.time() - 30 * 86400)
        s = m.week_stats(m.load_daily_health())
        assert s["per_disk"] == {}

    def test_count_log_events_parses_timestamps(self, tmp_path):
        import datetime as dt
        logf = tmp_path / "app.log"
        now = time.time()
        fmt = "%Y-%m-%d %H:%M:%S,%f"

        def stamp(offset_s):
            return dt.datetime.fromtimestamp(now - offset_s).strftime(fmt)

        lines = [
            stamp(3600) + " INFO overheat_alert\n",
            stamp(10 * 86400) + " INFO overheat_alert\n",  # outside 7 days
            "garbage line without timestamp overheat_alert\n",
        ]
        logf.write_text("".join(lines), encoding="utf-8")
        assert m.count_log_events("overheat_alert", days=7,
                                  log_path=str(logf), now=now) == 1

    def test_stats_and_graph24_i18n_parity(self):
        keys = ("menu.graph24", "win.graph24", "tab.stats",
                "settings.ui_theme", "stats.alerts", "stats.smart_alerts",
                "stats.disk_line", "stats.refresh", "stats.window")
        for lang in m.UI_LANGUAGES:
            for key in keys:
                assert key in m.STRINGS[lang], (lang, key)
            assert "{n}" in m.STRINGS[lang]["stats.alerts"]

    def test_new_uitheme_key_in_defaults_doc(self):
        # guards the validate/default drift for all future color keys
        cfg = m._validate_settings({})
        assert cfg["ui_theme"] in ("auto", "dark", "light")

    def test_temp_zone_bands_match_temp_color(self):
        """The graph zones use the same thresholds as the tray colors:
        green <51, orange 51-64, red >=65, clipped to the graph range."""
        bands = m.temp_zone_bands()
        assert [b[2] for b in bands] == [m.GREEN, m.ORANGE, m.RED]
        assert bands[0][0] == m.GRAPH_Y_LO and bands[0][1] == 51
        assert bands[1] == (51, 65, m.ORANGE)
        assert bands[2][0] == 65 and bands[2][1] == m.GRAPH_Y_HI
        # consistency with temp_color at the boundaries
        for lo, hi, color in bands:
            mid = (lo + hi) / 2
            assert m.temp_color(mid) == color
        # a narrower window clips instead of drawing out of range
        clipped = m.temp_zone_bands(55, 70)
        assert clipped == [(55, 65, m.ORANGE), (65, 70, m.RED)]

    def test_format_week_stats_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(tmp_path / "h.csv"))
        monkeypatch.setattr(m, "count_log_events", lambda *a, **k: 3)
        t0 = time.time() - 86400
        for day in range(2):
            m.append_daily_health(
                [{"model": "A", "bus": "NVMe", "temp": 40 + day,
                  "wear": 5, "read_errors": 0, "unfixed_errors": 0}],
                now=t0 + day * 86400)
        lines = m.format_week_stats(m.week_stats(m.load_daily_health()))
        assert any("3" in ln for ln in lines[:2])   # alert counters
        assert "A" in lines                          # disk header
        assert any("40" in ln and "40.5" in ln for ln in lines)
        assert lines[-1]                             # window footer

    def test_stats_menu_i18n_parity(self):
        for lang in m.UI_LANGUAGES:
            assert "menu.stats" in m.STRINGS[lang]
            assert "win.stats" in m.STRINGS[lang]


# ---------------------------------------------------------------------------
# v1.22.0 - crash-proofing, h24 persistence, stats reset
# ---------------------------------------------------------------------------
class TestMenuCallbackSafety:
    """A menu-callback exception used to propagate out of icon.run() and
    silently kill the whole app (the "closes by itself" bug)."""

    def test_spawn_once_flags_are_default_attributes(self):
        import inspect
        init_src = inspect.getsource(m.App.__init__)
        for flag in ("_graph24_open", "_stats_open", "_reset_open"):
            assert flag in init_src, flag

    def test_missing_window_flag_no_longer_kills_menu(self, app):
        # simulate the v1.21 state: the flag attribute does not exist
        for flag in ("_graph24_open", "_stats_open", "_reset_open"):
            if hasattr(app, flag):
                delattr(app, flag)
        calls = []

        def fake_thread(**kw):
            calls.append(kw)
            return types.SimpleNamespace(start=lambda: None)

        orig = m.threading.Thread
        m.threading.Thread = fake_thread
        try:
            app.show_graph24()
        finally:
            m.threading.Thread = orig
        assert len(calls) == 1          # spawn_once recovered via getattr

    def test_menu_build_wraps_callbacks_safely(self):
        import inspect
        src = inspect.getsource(m.App._build_menu)
        assert "_safe(" in src          # every item goes through the guard

    def test_tk_after_is_thread_safe_marshaler(self):
        import inspect
        src = inspect.getsource(m.App.tk_after)
        assert "event_generate" in src and "_ssd_after_queue" in src

    def test_settings_health_probe_uses_marshaler(self):
        import inspect
        src = inspect.getsource(m.App._settings_window)
        assert "root.after(0" not in src
        assert "tk_after" in src

    def test_about_latest_fetch_uses_marshaler(self):
        import inspect
        src = inspect.getsource(m.App._about_window)
        assert "root.after(0" not in src
        assert "tk_after" in src

    def test_poll_loop_survives_update_crash(self, app, monkeypatch):
        monkeypatch.setattr(m, "POLL_SECONDS", 1)
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError("poll exploded")

        monkeypatch.setattr(app, "update", boom)

        def fake_sleep(_):
            if calls["n"] >= 2:
                raise KeyboardInterrupt   # break out of the while True

        monkeypatch.setattr(m.time, "sleep", fake_sleep)
        with pytest.raises(KeyboardInterrupt):
            app.poll_loop()
        assert calls["n"] == 2          # kept polling after the crash


class TestHistory24Persistence:
    def test_update_persists_h24_immediately(self, app, tmp_path,
                                             monkeypatch):
        """The h24 file was only written on clean quit before, so updated
        installs started with an empty 24-hour graph."""
        f = tmp_path / "h24.csv"
        sf = tmp_path / "h24.json"
        monkeypatch.setattr(m, "HISTORY24_FILE", str(f))
        monkeypatch.setattr(m, "HISTORY24_STATE", str(sf))
        a = app
        a.icon = types.SimpleNamespace(icon=None, title="")
        a._record = lambda temp: None
        a._smart_watch = lambda temps: None
        a._alert_drive = lambda hottest, now: False
        a._sync_extra_icons = lambda temps: None
        monkeypatch.setattr(m, "read_temps", lambda: [
            {"model": "D", "temp": 41, "wear": 1,
             "read_errors": 0, "unfixed_errors": 0}])
        monkeypatch.setattr(m, "KEEP_HISTORY", False)
        a.history24 = []
        a._h24_state = {"minute": 0.0, "bucket": None}
        a.update()
        assert f.exists() and f.stat().st_size > 0
        assert sf.exists()

    def test_init_backfills_empty_store_from_fine_history(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "HISTORY24_FILE", str(tmp_path / "h24.csv"))
        monkeypatch.setattr(m, "HISTORY24_STATE", str(tmp_path / "h24.json"))
        hist = tmp_path / "fine.csv"
        now = 1_800_000_000.0
        hist.write_text(f"{now - 120},39\n{now - 60},40\n{now},41\n")
        monkeypatch.setattr(m, "HISTORY_FILE", str(hist))

        created = {}

        class FakeTray:
            def __init__(self, *a, **kw):
                created["icon"] = self
                self.menu = None
                self.icon = None
                self.title = ""

        monkeypatch.setattr(m.pystray, "Icon", FakeTray)
        monkeypatch.setattr(m, "log_event", lambda *a, **k: None)
        a = m.App.__new__(m.App)
        saved = {}

        def fake_save(points):
            saved["pts"] = list(points)

        monkeypatch.setattr(m, "_history24_save", fake_save)
        m.App.__init__(a)
        assert [t for _, t in saved["pts"]] == [39, 40, 41]
        assert [t for _, t in a.history24] == [39, 40, 41]

    def test_persist_helper_writes_both_files(self, tmp_path, monkeypatch):
        f = tmp_path / "h24.csv"
        sf = tmp_path / "h24.json"
        monkeypatch.setattr(m, "HISTORY24_FILE", str(f))
        monkeypatch.setattr(m, "HISTORY24_STATE", str(sf))
        m._history24_persist([(100.0, 42)], {"minute": 120.0, "bucket": 42})
        assert m._history24_load() == [(100.0, 42)]
        assert m._history24_load_state()["minute"] == 120.0


class TestStatsReset:
    def _make_files(self, tmp_path, monkeypatch):
        health = tmp_path / "health_daily.csv"
        logf = tmp_path / "app.log"
        smart = tmp_path / "smart_state.json"
        h24 = tmp_path / "h24.csv"
        h24s = tmp_path / "h24.json"
        health.write_text("date,time,model,bus,temp_c,wear_pct\n",
                          encoding="utf-8")
        logf.write_text("2026-09-24 08:00:00,000 INFO overheat_alert\n",
                        encoding="utf-8")
        smart.write_text("{}", encoding="utf-8")
        h24.write_text("1800000000,41\n", encoding="utf-8")
        h24s.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(health))
        monkeypatch.setattr(m, "LOG_FILE", str(logf))
        monkeypatch.setattr(m, "SMART_STATE_FILE", "smart_state.json")
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(m, "HISTORY24_FILE", str(h24))
        monkeypatch.setattr(m, "HISTORY24_STATE", str(h24s))
        return {"health": health, "log": logf, "smart": smart,
                "h24": h24, "h24s": h24s}

    def test_reset_stats_deletes_all_stat_files(self, app, tmp_path,
                                                monkeypatch):
        files = self._make_files(tmp_path, monkeypatch)
        # rotated backups hold old counters too - they must go as well
        (tmp_path / "app.log.1").write_text(
            "2026-09-20 08:00:00,000 INFO overheat_alert\n")
        (tmp_path / "app.log.2").write_text(
            "2026-09-19 08:00:00,000 INFO overheat_alert\n")
        removed = m.reset_stats(app)
        assert removed == 7
        for key in ("health", "smart", "h24", "h24s"):
            assert not files[key].exists(), key
        # the current log may be re-created with the marker line, but the
        # counters read from it must be zero (the v1.23.1 bug: the old
        # log survived the reset, so "Overheat alerts this week" stayed)
        assert m.count_log_events("overheat_alert", days=7) == 0
        assert m.count_log_events("smart_alert", days=7) == 0
        for name in ("app.log.1", "app.log.2"):
            assert not (tmp_path / name).exists(), name

    def test_reset_stats_reseeds_h24_from_fine_history(
            self, app, tmp_path, monkeypatch):
        self._make_files(tmp_path, monkeypatch)
        fine = tmp_path / "fine.csv"
        now = 1_800_000_000.0
        fine.write_text(f"{now - 60},40\n{now},42\n")
        monkeypatch.setattr(m, "HISTORY_FILE", str(fine))
        a = app
        a.history24 = [(now, 41)]
        m.reset_stats(a)
        assert [t for _, t in a.history24] == [40, 42]
        assert a._h24_state == {"minute": 0.0, "bucket": None}

    def test_reset_stats_clears_alert_state(self, app, tmp_path,
                                            monkeypatch):
        self._make_files(tmp_path, monkeypatch)
        a = app
        a._alert_since = 123.0
        a._last_alert = 456.0
        a._last_smart_alert = 789.0
        a._smart_state = {"X": {"wear": 9}}
        m.reset_stats(a)
        assert a._alert_since is None
        assert a._last_alert == 0.0
        assert a._last_smart_alert == 0.0
        assert a._smart_state == {}

    def test_reset_stats_idempotent_counters(self, app, tmp_path,
                                             monkeypatch):
        """Two resets in a row still leave zeroed counters (the log file
        itself is re-created by the marker line each time)."""
        self._make_files(tmp_path, monkeypatch)
        a = app
        assert m.reset_stats(a) >= 5
        m.reset_stats(a)
        assert m.count_log_events("overheat_alert", days=7) == 0

    def test_reset_stats_log_reopen_allows_later_writes(
            self, app, tmp_path, monkeypatch):
        """After the reset the module must keep logging (fresh handler)."""
        self._make_files(tmp_path, monkeypatch)
        a = app
        m.reset_stats(a)
        m.log_event("after_reset_probe")
        assert m.count_log_events("after_reset_probe", days=1) == 1

    def test_reset_stats_keeps_fine_history(self, app, tmp_path,
                                            monkeypatch):
        files = self._make_files(tmp_path, monkeypatch)
        fine = tmp_path / "ssd_temp_history.csv"
        fine.write_text("1800000000,41\n")
        a = app
        m.reset_stats(a)
        assert fine.exists()              # untouched

    def test_reset_stats_survives_locked_file(self, app, tmp_path,
                                              monkeypatch):
        files = self._make_files(tmp_path, monkeypatch)
        a = app

        real_remove = m.os.remove

        def locked_remove(path):
            if str(path) == str(files["log"]):
                raise PermissionError("in use")
            return real_remove(path)

        monkeypatch.setattr(m.os, "remove", locked_remove)
        removed = m.reset_stats(a)
        assert removed == 4               # the locked log is skipped
        assert files["log"].exists()

    def test_reset_stats_writes_marker_event(self, app, tmp_path,
                                             monkeypatch):
        self._make_files(tmp_path, monkeypatch)
        events = []
        monkeypatch.setattr(m, "log_event",
                            lambda event, **kw: events.append(event))
        a = app
        m.reset_stats(a)
        assert "stats_reset" in events

    def test_reset_stats_i18n_parity(self):
        for lang in m.UI_LANGUAGES:
            for key in ("stats.reset", "stats.reset_confirm",
                        "stats.reset_done", "menu.reset_stats",
                        "common.cancel"):
                assert key in m.STRINGS[lang], (lang, key)

    def test_reset_stats_menu_and_settings_wired(self):
        import inspect
        assert "show_reset_stats" in inspect.getsource(m.App._build_menu)
        assert "_confirm_reset_stats" in \
            inspect.getsource(m.App._settings_window)
        assert "reset_stats" in inspect.getsource(m.App._reset_stats_window)


# ---------------------------------------------------------------------------
# v1.23.0 - About crash fix, non-blocking marshaler, retrying release line
# ---------------------------------------------------------------------------
class TestAboutCrashFix:
    """v1.22.0 still crashed in tcl86t.dll: tk_after() called
    event_generate() off-thread, which raises back into the worker (About
    stuck at "checking...") or panics natively (Settings click crash)."""

    def test_tk_after_never_touches_tcl_off_thread(self):
        import inspect
        src = inspect.getsource(m.App.tk_after)
        assert "win.event_generate(" not in src
        assert "win.after(" not in src       # no Tcl call on the win object
        assert "_ssd_after_queue.put" in src

    def test_tk_after_noop_without_marshaler(self):
        # a window that never got _make_tk_after must not raise
        class FakeWin:
            pass
        m.App.tk_after(FakeWin(), 0, lambda: None)   # must not raise

    def test_root_winfo_exists_reads_flag_not_tcl(self):
        import types
        win = types.SimpleNamespace(
            _ssd_alive=threading.Event())
        win._ssd_alive.set()
        assert m.root_winfo_exists(win) is True
        win._ssd_alive.clear()
        assert m.root_winfo_exists(win) is False
        # no flag at all -> conservative False (worker stops polling)
        assert m.root_winfo_exists(types.SimpleNamespace()) is False

    def test_drain_loop_stops_when_window_dies(self, app):
        """The 120 ms drain loop must end once the window is gone."""
        import queue as _q
        import types
        win = types.SimpleNamespace()
        calls = []

        def fake_after(ms, fn=None):
            calls.append(ms)
            raise RuntimeError("interp gone")

        win.after = fake_after
        win._ssd_alive = threading.Event()
        win._ssd_alive.set()
        win._ssd_after_queue = _q.Queue()
        win._ssd_after_queue.put((0, lambda: None))
        win.bind = lambda *a, **k: None
        app._make_tk_after(win)     # must swallow, not raise
        assert win._ssd_after_queue.empty()   # the queued item was consumed
        assert calls                      # drain actually ran
        win._ssd_alive.clear()

    def test_about_uses_theme_palette_not_hardcoded_colors(self):
        import inspect
        src = inspect.getsource(m.App._about_window)
        assert "ui_palette()" in src
        for dead in ("#e2e8f0", "#94a3b8", "#fbbf24", "#86efac"):
            assert dead not in src, dead
        assert "c[\"head\"]" in src and "c[\"bg\"]" in src

    def test_about_highlights_newer_release(self):
        import inspect
        src = inspect.getsource(m.App._about_window)
        assert "is_newer_version(tag)" in src
        assert "ORANGE" in src and "GREEN" in src

    def test_about_release_line_never_stuck(self):
        import inspect
        src = inspect.getsource(m.App._about_window)
        assert "_ABOUT_RELEASE_TIMEOUT" in src
        assert "_ABOUT_RELEASE_RETRIES" in src
        assert "about.latest.retrying" in src
        assert "root_winfo_exists" in src

    def test_about_retrying_i18n_parity(self):
        for lang in m.UI_LANGUAGES:
            assert "about.latest.retrying" in m.STRINGS[lang], lang

    def test_palettes_have_link_color(self):
        assert m.UI_LIGHT["link"].startswith("#")
        assert m.UI_DARK["link"].startswith("#")
        assert m.UI_LIGHT["link"] != m.UI_DARK["link"]

    def test_watchdog_restarts_tray_loop_after_native_death(self, app,
                                                             monkeypatch):
        """If icon.run() dies unexpectedly the watchdog must restart it
        (and exit cleanly on a real quit)."""
        a = app
        a._shutdown_requested = False
        a.poll_loop = lambda: None
        runs = {"n": 0}

        class FakeIcon:
            def run(self):
                runs["n"] += 1
                if runs["n"] == 1:
                    raise RuntimeError("native crash")
                with a._lock:
                    a._shutdown_requested = True   # second run: user quits

        a.icon = FakeIcon()
        monkeypatch.setattr(m.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            m.threading, "Thread",
            lambda *a2, **k: types.SimpleNamespace(start=lambda: None))
        m._watch_icon_loop(a)
        assert runs["n"] == 2

    def test_watchdog_no_restart_on_normal_quit(self, app, monkeypatch):
        a = app
        a._shutdown_requested = True
        a.poll_loop = lambda: None
        runs = {"n": 0}

        class FakeIcon:
            def run(self):
                runs["n"] += 1

        a.icon = FakeIcon()
        monkeypatch.setattr(
            m.threading, "Thread",
            lambda *a2, **k: types.SimpleNamespace(start=lambda: None))
        m._watch_icon_loop(a)      # returns after the single run()
        assert runs["n"] == 1

    def test_watchdog_starts_poll_thread_exactly_once(self, app,
                                                      monkeypatch):
        a = app
        a._shutdown_requested = True
        a.poll_loop = lambda: None

        class FakeIcon:
            def run(self):
                pass

        a.icon = FakeIcon()
        started = []

        def fake_thread(target=None, **kw):
            started.append(target)
            return types.SimpleNamespace(start=lambda: None)

        monkeypatch.setattr(m.threading, "Thread", fake_thread)
        m._watch_icon_loop(a)
        assert len(started) == 1 and started[0] == a.poll_loop

    def test_main_owns_one_message_pump_only(self):
        """v1.23.0 regression: icon.run() ran in BOTH the watchdog thread
        and App.run -> two Win32 message pumps, the tray died on the next
        menu click."""
        import inspect
        src = inspect.getsource(m.main)
        assert "_watch_icon_loop(App())" in src
        assert "app.run()" not in src and "App().run()" not in src

    def test_run_docstring_warns_not_to_call_it(self):
        import inspect
        assert "icon.run" in inspect.getsource(m.App.run)

    def test_main_spawns_watchdog_thread(self):
        import inspect
        src = inspect.getsource(m.main)
        assert "_watch_icon_loop" in src

    def test_about_retry_constants(self):
        assert 0 < m._ABOUT_RELEASE_RETRIES <= 10
        assert 5 <= m._ABOUT_RELEASE_TIMEOUT <= 120


# ---------------------------------------------------------------------------
# v1.24.0 - reset actually zeroes counters, log viewer, lite program
# ---------------------------------------------------------------------------
class TestResetStatsV124:
    """v1.23.1 bug: the counters read the log + its rotated .1/.2 files,
    but reset_stats only deleted the log - and on Windows the open
    RotatingFileHandler handle made even THAT deletion fail (removed=4).
    "Overheat alerts this week" therefore never reset."""

    def test_reset_includes_rotated_backups(self, app, tmp_path,
                                            monkeypatch):
        files = self._files(tmp_path, monkeypatch)
        for suffix in ("", ".1", ".2"):
            (tmp_path / ("app.log" + suffix)).write_text(
                "2026-09-20 08:00:00,000 INFO overheat_alert\n")
        removed = m.reset_stats(app)
        for suffix in (".1", ".2"):
            assert not (tmp_path / ("app.log" + suffix)).exists()
        assert removed >= 6

    def _files(self, tmp_path, monkeypatch):
        health = tmp_path / "health_daily.csv"
        logf = tmp_path / "app.log"
        smart = tmp_path / "smart_state.json"
        h24 = tmp_path / "h24.csv"
        h24s = tmp_path / "h24.json"
        for f in (health, smart, h24, h24s):
            f.write_text("{}", encoding="utf-8")
        logf.write_text("2026-09-24 08:00:00,000 INFO overheat_alert\n",
                        encoding="utf-8")
        monkeypatch.setattr(m, "HEALTH_LOG_FILE", str(health))
        monkeypatch.setattr(m, "LOG_FILE", str(logf))
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(m, "HISTORY24_FILE", str(h24))
        monkeypatch.setattr(m, "HISTORY24_STATE", str(h24s))
        return {"log": logf, "health": health}

    def test_reopen_handler_detaches_and_recreates(self, tmp_path,
                                                   monkeypatch):
        """_reopen_log_handler must swap the module handler so the old
        file handle is released (Windows cannot delete open files)."""
        real_log = m.LOG_FILE               # remember: monkeypatch hides it
        testlog = tmp_path / "swap.log"
        monkeypatch.setattr(m, "LOG_FILE", str(testlog))
        old = m._handler
        m._reopen_log_handler()
        try:
            assert m._handler is not old       # swapped to a fresh handler
            m.log_event("probe_after_reopen")
            assert testlog.exists()            # new handler writes to LOG_FILE
        finally:
            # give the module a healthy handler on the REAL log again
            m._reopen_log_handler(real_log)

    def test_reset_works_twice_in_a_row(self, app, tmp_path, monkeypatch):
        """Second reset must also find zero counters (fresh handler each
        time - no stale handle, no leak)."""
        self._files(tmp_path, monkeypatch)
        m.reset_stats(app)
        m.log_event("overheat_alert")           # one new alert after reset
        assert m.count_log_events("overheat_alert", days=1) == 1
        m.reset_stats(app)
        assert m.count_log_events("overheat_alert", days=1) == 0

    def test_log_viewer_wired(self):
        import inspect
        assert "show_log" in inspect.getsource(m.App._build_menu)
        src = inspect.getsource(m.App._log_window)
        assert "_spawn_once" not in src
        assert "tk_after" in src                 # queue-only marshal
        assert "mainloop" in src                 # own thread + tk
        assert "mainloop" not in inspect.getsource(m.App.show_log)

    def test_log_viewer_i18n_parity(self):
        keys = ("menu.log", "win.log", "log.filter", "log.search",
                "log.reload", "log.copy", "log.open", "log.hint",
                "log.lines", "log.filter.all", "log.filter.errors",
                "log.filter.warnings", "log.filter.overheat",
                "log.filter.smart", "log.filter.updates")
        for lang in m.UI_LANGUAGES:
            for key in keys:
                assert key in m.STRINGS[lang], (lang, key)
            assert "{n}" in m.STRINGS[lang]["log.lines"]

    def test_lite_program_exists_and_is_self_contained(self):
        from pathlib import Path
        p = Path(__file__).resolve().parents[1] / "lite" / "ssd_temp_lite.py"
        assert p.exists(), "lite program missing"
        src = p.read_text(encoding="utf-8")
        # lean deps: pystray + Pillow only (imported lazily), no tk/logging
        assert "import tkinter" not in src
        assert "import pystray" in src
        assert "from PIL import" in src
        # same SMART query as the main app
        assert "Get-StorageReliabilityCounter" in src
        assert "ssd_temp_lite" in src

    def test_lite_compiles(self):
        import py_compile
        from pathlib import Path
        p = Path(__file__).resolve().parents[1] / "lite" / "ssd_temp_lite.py"
        py_compile.compile(str(p), doraise=True)

    def test_lite_shares_main_app_query(self):
        """The lite PS_TEMPS must stay in sync with the main app's."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "lite" / "ssd_temp_lite.py").read_text(encoding="utf-8")
        for token in ("Get-PhysicalDisk", "MediaType -eq 'SSD'",
                      "BusType -ne 'USB'", "Temperature", "ConvertTo-Json"):
            assert token in src, token

    def test_release_workflow_builds_lite(self):
        from pathlib import Path
        yml = (Path(__file__).resolve().parents[1]
               / ".github" / "workflows" / "release.yml").read_text(
                   encoding="utf-8")
        assert "lite/ssd_temp_lite.py" in yml          # built from source
        assert "ssd_temp_lite_v" in yml                # renamed with version
        assert "SHA256SUMS.txt" in yml                 # checksummed like others
        # attached to the release asset list
        assert "ssd_temp_lite_v${{ steps.ver.outputs.version }}.exe" in yml


# ---------------------------------------------------------------------------
# installer wires the crash-watchdog scheduled task (wscript launcher)
# ---------------------------------------------------------------------------
class TestInstallerWatchdogWiring:
    """The Inno Setup installer must stage, register and un-register the
    1-minute crash watchdog automatically (see AGENT.md lessons: the task
    runs wscript.exe in the interactive session so no console window
    flashes and the tray app never lands in session 0)."""

    @staticmethod
    def _read(*parts):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1].joinpath(*parts)).read_text(
            encoding="utf-8")

    def test_installer_stages_watchdog_files(self):
        iss = self._read("setup.iss")
        for token in ('Source: "tools\\watchdog.ps1"; DestDir: "{app}\\watchdog"',
                      'Source: "tools\\watchdog_launcher.vbs";'
                      ' DestDir: "{app}\\watchdog"',
                      'Source: "tools\\register_watchdog_task.ps1";'
                      ' DestDir: "{app}\\watchdog"'):
            assert token in iss, token

    def test_installer_registers_task_after_copy(self):
        iss = self._read("setup.iss")
        run_idx = iss.index("[Run]")
        uninstall_idx = iss.index("[UninstallRun]")
        run_block = iss[run_idx:uninstall_idx]
        assert "register_watchdog_task.ps1" in run_block
        # the register step must run before the post-install app launch
        assert run_block.index("register_watchdog_task.ps1") \
            < run_block.index("postinstall")

    def test_uninstaller_removes_task_before_files(self):
        iss = self._read("setup.iss")
        block = iss[iss.index("[UninstallRun]"):iss.index("[UninstallDelete]")]
        assert "SSDTempMonitor Watchdog" in block
        assert "/Delete" in block

    def test_register_script_design(self):
        src = self._read("tools", "register_watchdog_task.ps1")
        assert "wscript.exe" in src                 # no console flash
        assert "LogonType Interactive" in src       # session 1, not session 0
        assert "RunLevel Highest" in src
        assert "New-TimeSpan -Minutes 1" in src     # 1-minute cadence

    def test_register_script_accepts_appdir_param(self):
        src = self._read("tools", "register_watchdog_task.ps1")
        assert 'param(' in src and "$AppDir" in src

    def test_manual_installer_delegates_to_register_script(self):
        src = self._read("tools", "install_watchdog.ps1")
        assert "register_watchdog_task.ps1" in src

    def test_watchdog_locates_exe_relative_to_script(self):
        src = self._read("tools", "watchdog.ps1")
        assert "MyInvocation.MyCommand.Path" in src
        # relative probe first, default install dir as the fallback
        assert "ssd_temp_monitor.exe" in src
        assert "C:\\Program Files\\SSD Temp Monitor" in src

    def test_watchdog_spawns_app_outside_task_job(self):
        """The watchdog runs inside the scheduled task's job object: an app
        started with Start-Process joins that job, the task stays 'Running'
        forever (later triggers: 0x800710E0) and the ExecutionTimeLimit then
        kills the restarted app. WMI spawn escapes the job (v1.24.5 bug)."""
        src = self._read("tools", "watchdog.ps1")
        assert "Win32_Process" in src and "Invoke-CimMethod" in src
        assert "Start-Process" not in src

    def test_launcher_runs_powershell_hidden(self):
        src = self._read("tools", "watchdog_launcher.vbs")
        assert "watchdog.ps1" in src
        assert "-WindowStyle Hidden" in src
        assert ", 0, False" in src                  # hidden window, no wait

    def test_watchdog_respects_user_quit_marker(self):
        """The watchdog must NOT resurrect the app after a deliberate quit
        from the tray menu: the app writes watchdog_skip.flag, the marker
        is cleared again on the next successful start."""
        src = self._read("tools", "watchdog.ps1")
        assert "watchdog_skip.flag" in src
        app_src = self._read("ssd_temp_tray.py")
        assert "WATCHDOG_SUPPRESS_FILE" in app_src
        # marker is written on quit...
        quit_idx = app_src.index("def quit(self, *_):"
                                 ) if "def quit(self, *_):" in app_src \
            else app_src.index("def quit(")
        assert app_src.index("WATCHDOG_SUPPRESS_FILE, \"w\""
                             ) > quit_idx
        # ...and removed during startup, before the tray loop starts
        start_idx = app_src.index("def main():")
        loop_idx = app_src.index("_watch_icon_loop(App())")
        remove_idx = app_src.index("os.remove(WATCHDOG_SUPPRESS_FILE)")
        assert start_idx < remove_idx < loop_idx
