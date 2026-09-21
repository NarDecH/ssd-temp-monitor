"""Unit tests for ssd_temp_tray.py — no GUI, no PowerShell, no admin needed.

The PowerShell boundary is always monkeypatched; only pure logic and the
CSV/Windows-mutex helpers are exercised for real.
"""
import csv
import sys
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
