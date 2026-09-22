"""Session-wide test isolation for the app's real-file side effects.

The module-level logger "ssd_temp_monitor" carries a RotatingFileHandler
pointed at the user's real %APPDATA% log. During tests every log_event()
call would append there. Detach file handlers for the whole session and
restore them afterwards so tests can assert on log output via caplog
without ever touching the real file.
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssd_temp_tray as m  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _detach_real_log_handler():
    logger = logging.getLogger("ssd_temp_monitor")
    file_handlers = [h for h in logger.handlers
                     if isinstance(h, logging.FileHandler)]
    for h in file_handlers:
        logger.removeHandler(h)
        h.close()
    yield
    for h in file_handlers:
        logger.addHandler(h)


@pytest.fixture(autouse=True)
def _clean_global_state():
    """Reset SETTINGS to defaults and clear the icon cache around every test.

    Resetting (not merely snapshot/restoring) matters: SETTINGS at import
    time is loaded from the developer's real config, so tests that rely on
    baseline values (e.g. digit_color == "auto" -> dark digits on the
    green pill) would fail on machines whose real config sets a custom
    value. Snapshot/restore alone also lets one leaking test pollute
    later ones depending on execution order. Session-wide (in conftest)
    so it covers every test module, not just test_ssd_temp_tray.py.
    """
    saved = dict(m.SETTINGS)
    m.SETTINGS.clear()
    m.SETTINGS.update(m.DEFAULT_SETTINGS)
    m._ICON_CACHE.clear()
    yield
    m.SETTINGS.clear()
    m.SETTINGS.update(saved)
    m._ICON_CACHE.clear()
