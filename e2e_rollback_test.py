"""E2E rollback proof with the REAL shim/watchdog on a real filesystem.

Scenario A (broken update): the pending marker survives the grace period
-> the detached watchdog must restore the backup exe, drop the REPORTED
marker, relaunch, and the app must blacklist that version afterwards.

Scenario B (healthy update): a helper clears the marker within the grace
period -> the watchdog must do nothing (no report, backup untouched).

Uses a sandbox exe name that cannot match the real tray app and shrinks
the grace period to 20 s to keep the round fast.
"""
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest.mock as um

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import ssd_temp_tray as m  # noqa: E402

APP_NAME = "ssd_temp_monitor_e2e.exe"
# real interpreter for helper children (sys.executable gets sandboxed
# inside the scenarios, so capture it at import time)
PYEXE = sys.executable
# ping.exe prints usage and EXITS immediately when launched without args
# (a bare cmd.exe would sit waiting for stdin and linger as a process)
STUB_SRC = os.path.join(os.environ["WINDIR"], "System32", "ping.exe")
GRACE = 20


def log(*a):
    print(*a, flush=True)


def run_shim(shim, timeout=120):
    """Run the shim WITHOUT pipes: the shim `start /B`-launches explorer
    and the watchdog, which inherit stdout. With capture_output=True the
    inherited write-ends keep the pipe open forever and communicate()
    never sees EOF even after the shim itself exits (real hang found by
    this test). Redirecting to a file only blocks on the cmd process."""
    out = os.path.join(tempfile.gettempdir(), "e2e_shim_out.txt")
    with open(out, "w") as f:
        proc = subprocess.run(["cmd", "/c", shim], stdout=f,
                              stderr=subprocess.STDOUT, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    return proc.returncode


def silence_messagebox():
    # scenario A makes begin_healthy_session() pop a real MessageBoxW on
    # the desktop - stub it out so the E2E stays headless
    um.patch.object(m.ctypes.windll.user32, "MessageBoxW",
                    lambda *a, **k: 0).start()


def build_sandbox(tmp):
    # clear any leftover stub from an earlier attempt first - it would
    # otherwise match the shim's IMAGENAME wait loop
    subprocess.run(["taskkill", "/F", "/IM", APP_NAME],
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    app_dir = os.path.join(tmp, "app")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(app_dir)
    os.makedirs(data_dir)
    # different basename so the shim's IMAGENAME wait loop cannot match the
    # REAL tray app running on this machine (dev-machine safety)
    app = os.path.join(app_dir, APP_NAME)
    shutil.copyfile(STUB_SRC, app)
    return app, app_dir, data_dir


def apply_sandbox(m, app, data_dir):
    m.DATA_DIR = data_dir
    m.sys.frozen = True
    m.sys.executable = app
    m.ROLLBACK_GRACE_SECONDS = GRACE
    # keep the E2E off the user's real event log file (same trick as
    # tests/conftest.py): detach the rotating file handler for the run
    for h in list(m._event_log.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            m._event_log.removeHandler(h)
            h.close()


def wait_for(cond, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def kill_stub_wait(max_wait=15):
    """Kill every sandbox stub and wait until it is REALLY gone.

    The rollback path relaunches the app via explorer, which starts a
    moment after the watchdog self-deletes - a single taskkill here can
    race the spawn and leave the exe locked for the tmpdir cleanup.
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        subprocess.run(["taskkill", "/F", "/IM", APP_NAME],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        chk = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {APP_NAME}"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if APP_NAME not in (chk.stdout or ""):
            return True
        time.sleep(1)
    return False


def scenario_broken(tmp):
    app, app_dir, data_dir = build_sandbox(tmp)
    saved = (m.DATA_DIR, getattr(m.sys, "frozen", False), m.sys.executable,
             m.ROLLBACK_GRACE_SECONDS)
    apply_sandbox(m, app, data_dir)
    try:
        assert m.stage_backup_for_rollback(
            app_exe=app, data_dir=data_dir, target_version="v1.15.0-bad")
        # "broken update installed": exe replaced by a stub that exits
        # immediately and never clears the pending marker
        shutil.copyfile(STUB_SRC, app)
        shim = m.build_update_shim(os.path.join(tmp, "setup.exe"),
                                   app_exe_path=app, restart_path=app)
        t0 = time.time()
        rc = run_shim(shim)
        elapsed = time.time() - t0
        wd = os.path.join(app_dir, m.ROLLBACK_WATCHDOG_NAME)
        wd_gone = wait_for(lambda: not os.path.exists(wd), GRACE + 45)
        backup_gone = not os.path.exists(
            os.path.join(app_dir, m.ROLLBACK_BACKUP_NAME))
        reported = os.path.exists(os.path.join(data_dir, m.ROLLBACK_REPORTED))
        pending_still = os.path.exists(
            os.path.join(data_dir, m.ROLLBACK_PENDING))
        log(f"A: rc={rc} shim={elapsed:.0f}s "
            f"watchdog_selfdeleted={wd_gone} backup_gone={backup_gone} "
            f"reported={reported} pending_still={pending_still}")
        ok = (elapsed < GRACE and wd_gone and backup_gone and reported
              and pending_still)
        # the relaunched OLD app reads the report and blacklists the version
        silence_messagebox()
        with um.patch.object(m, "_rollback_marker_path",
                             lambda name: os.path.join(data_dir, name)):
            m.begin_healthy_session()
            blacklisted = m.version_is_broken("v1.15.0-bad")
        log(f"A: blacklisted={blacklisted}")
        return ok and blacklisted
    finally:
        kill_stub_wait()
        (m.DATA_DIR, m.sys.frozen, m.sys.executable,
         m.ROLLBACK_GRACE_SECONDS) = saved


def scenario_healthy(tmp):
    app, app_dir, data_dir = build_sandbox(tmp)
    saved = (m.DATA_DIR, getattr(m.sys, "frozen", False), m.sys.executable,
             m.ROLLBACK_GRACE_SECONDS)
    apply_sandbox(m, app, data_dir)
    try:
        assert m.stage_backup_for_rollback(
            app_exe=app, data_dir=data_dir, target_version="v1.15.0")
        marker = os.path.join(data_dir, m.ROLLBACK_PENDING)
        deleter = subprocess.Popen(
            [PYEXE, "-c",
             "import time,os,sys; time.sleep(5); os.remove(sys.argv[1])",
             marker],
            creationflags=subprocess.CREATE_NO_WINDOW)
        shim = m.build_update_shim(os.path.join(tmp, "setup.exe"),
                                   app_exe_path=app, restart_path=app)
        t0 = time.time()
        rc = run_shim(shim)
        elapsed = time.time() - t0
        wd = os.path.join(app_dir, m.ROLLBACK_WATCHDOG_NAME)
        wait_for(lambda: not os.path.exists(wd), GRACE + 45)
        reported = os.path.exists(os.path.join(data_dir, m.ROLLBACK_REPORTED))
        backup_still = os.path.exists(
            os.path.join(app_dir, m.ROLLBACK_BACKUP_NAME))
        log(f"B: rc={rc} shim={elapsed:.0f}s "
            f"reported={reported} backup_still={backup_still}")
        deleter.kill()
        return (not reported) and backup_still and elapsed < GRACE
    finally:
        kill_stub_wait()
        (m.DATA_DIR, m.sys.frozen, m.sys.executable,
         m.ROLLBACK_GRACE_SECONDS) = saved


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        a = scenario_broken(tmp)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        b = scenario_healthy(tmp)
    kill_stub_wait()
    log(f"VERDICT: broken-restore={'PASS' if a else 'FAIL'} "
        f"healthy-noop={'PASS' if b else 'FAIL'}")
    sys.exit(0 if (a and b) else 1)
