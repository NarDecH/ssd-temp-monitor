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
