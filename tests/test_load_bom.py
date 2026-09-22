"""Regression tests (v1.14.0 round): BOM-tolerant config + update-flow isolation."""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ssd_temp_tray as m  # noqa: E402


class TestConfigBom:
    def test_bom_config_still_loads(self, tmp_path):
        # PowerShell 5.1 Set-Content -Encoding UTF8 writes a BOM; the app
        # must not silently reset every setting when it sees one.
        p = tmp_path / "config.json"
        p.write_bytes(b"\xef\xbb\xbf" + b'{"poll_seconds": 3}')
        cfg = m.load_settings(str(p))
        assert cfg["poll_seconds"] == 3

    def test_plain_config_still_loads(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_bytes(b'{"poll_seconds": 4}')
        assert m.load_settings(str(p))["poll_seconds"] == 4

    def test_bom_wrong_type_ignored(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_bytes(b"\xef\xbb\xbf" + b'{"poll_seconds": "fast"}')
        cfg = m.load_settings(str(p))
        assert cfg["poll_seconds"] == m.DEFAULT_SETTINGS["poll_seconds"]


class TestUpdateFlowIsolation:
    def test_run_unattended_writes_only_to_temp_dir(self, tmp_path, monkeypatch, capsys):
        """run_unattended_update must not write the setup file outside TEMP,
        and never into the tester's real TEMP."""
        import hashlib

        body = b"INSTALLER"
        hexd = hashlib.sha256(body).hexdigest()
        responses = {
            "https://x/setup.exe": body,
            "https://x/SHA256SUMS.txt": f"{hexd}  setup.exe\n".encode(),
        }

        def fake_urlopen(req, timeout=10):
            payload = responses[str(req.full_url)]
            return type("R", (), {"read": lambda self: payload,
                                  "__enter__": lambda self: self,
                                  "__exit__": lambda self, *a: False})()

        monkeypatch.setattr(m, "fetch_latest_release", lambda repo, include_prereleases=False: {
            "tag_name": "v9.0.0",
            "assets": [{"name": "setup.exe", "state": "uploaded",
                        "browser_download_url": "https://x/setup.exe"}]})
        monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(m, "build_update_shim", lambda dest, restart_path=None: "SHIM.cmd")
        monkeypatch.setattr(m.subprocess, "Popen", lambda cmd, **k: None)
        monkeypatch.setenv("TEMP", str(tmp_path))
        with pytest.raises(SystemExit) as ei:
            m.run_unattended_update()
        assert ei.value.code == 0
        # the download landed inside the sandboxed TEMP, named after the version
        written = list(tmp_path.glob("ssd_temp_monitor_setup_*.exe"))
        assert [f.name for f in written] == ["ssd_temp_monitor_setup_v9.0.0.exe"]

    def test_log_event_does_not_touch_real_log_in_tests(self, caplog):
        """log_event during tests must be captured, not appended to the
        user's real rotating event log file."""
        real_log = os.path.join(os.environ.get("APPDATA", ""), "SSDTempMonitor",
                                "ssd_temp_monitor.log")
        before = os.path.getsize(real_log) if os.path.exists(real_log) else 0
        with caplog.at_level(logging.INFO, logger="ssd_temp_monitor"):
            m.log_event("isolation_probe", marker="bom-round")
        after = os.path.getsize(real_log) if os.path.exists(real_log) else 0
        assert after == before
        assert any("isolation_probe" in r.getMessage() for r in caplog.records)
