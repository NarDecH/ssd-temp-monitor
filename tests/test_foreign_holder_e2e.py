"""Integration test: a REAL foreign process holds the AppMutex.

Launches a detached python that opens the app's named mutex and sleeps,
then runs this app as a subprocess with --duplicate-silent and asserts
the v1.25.3 contract end-to-end:
  * exit code 2 (duplicate), no dialog (silent flag)
  * the child's event log records ``mutex_suspect`` (forensics BEFORE
    the dialog would be shown)
  * the sampled suspects include the foreign holder's cmdline marker

The child's DATA_DIR is redirected to a temp dir via SSD_TEMP_DATA_DIR
so the user's real %APPDATA% log is never touched. The test mutex name
carries a unique cmdline marker so a concurrently running REAL app can
never satisfy the "a real instance holds it" shortcut. Cleanup is
deterministic (holder killed in finally).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP = "ssd_temp_tray.py"

HOLDER_SCRIPT = """# %(marker)s
import time
import ctypes
h = ctypes.windll.kernel32.CreateMutexW(None, False, r'%(mutex)s')
print('held', flush=True)
time.sleep(120)
"""


def _spawn_holder(mutex_name, marker):
    """Detached python holding the mutex, marker on its command line."""
    body = HOLDER_SCRIPT % {"marker": marker, "mutex": mutex_name}
    return subprocess.Popen(
        [sys.executable, "-c", body],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW)


@pytest.mark.skipif(sys.platform != "win32",
                    reason="real-mutex test is Windows-only")
def test_foreign_holder_duplicate_start_is_silent_and_logged(
        tmp_path, monkeypatch):
    """End-to-end: foreign holder -> duplicate logs mutex_suspect, exit 2."""
    import ssd_temp_tray as m  # noqa: F401  (verify import surface)

    # the child resolves MUTEX_NAME from this env var; the UNIQUE marker
    # embedded in the name is the cmdline token _app_process_count
    # matches, so the real app on this machine cannot mask the holder
    marker = f"SSD_FOREIGN_HOLDER_MARKER_{os.getpid()}"
    mutex_name = f"Local\\ssd_temp_dup_test_{marker}"
    monkeypatch.setenv("SSD_TEMP_MUTEX_NAME", mutex_name)
    monkeypatch.setattr(m, "MUTEX_NAME", mutex_name)

    holder = _spawn_holder(mutex_name, marker)
    try:
        line = holder.stdout.readline() if holder.stdout else ""
        assert line.strip().startswith("held"), \
            "holder failed to take the mutex"

        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / APP), "--duplicate-silent"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "SSD_TEMP_SILENT_DUPLICATE": "1",
                 "SSD_TEMP_DATA_DIR": str(tmp_path),
                 "SSD_TEMP_MUTEX_NAME": mutex_name,
                 # matches ONLY the app-under-test cmdline (self is
                 # excluded by pid; the holder runs ``-c <marker>`` and
                 # a real installed app has no such cmdline) so the
                 # count is 0 and forensics MUST fire
                 "SSD_TEMP_COUNT_MARKER": f"{APP} --duplicate-silent"})

        assert result.returncode == 2, (
            f"duplicate must exit 2, got {result.returncode}: "
            f"{result.stderr[-400:]}")

        log_path = tmp_path / "ssd_temp_monitor.log"
        assert log_path.exists(), "child wrote no event log at all"
        text = log_path.read_text(encoding="utf-8")
        suspect_lines = [ln for ln in text.splitlines()
                         if "mutex_suspect" in ln]
        assert suspect_lines, "forensics missing for foreign holder"
        assert marker in suspect_lines[-1], (
            "foreign holder process was not among the sampled suspects")
    finally:
        holder.kill()
        holder.wait(timeout=10)
