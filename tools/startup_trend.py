#!/usr/bin/env python3
"""Startup-latency trend from the event log (no admin needed).

Reads ``startup ... ms=`` events and prints per-version statistics:

    python tools/startup_trend.py            # last 14 days
    python tools/startup_trend.py --days 3
    python tools/startup_trend.py --json     # machine-readable

v1.25.8+ writes ``startup version=X ms=N`` (interpreter start -> tray
object construction). Older lines without ms are skipped, so the very
first days after upgrading will have few samples - that is expected.
Exit code is always 0 (report-only tool).
"""
import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta

APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
LOG = os.path.join(APPDATA, "SSDTempMonitor", "ssd_temp_monitor.log")
LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO startup "
    r"version=([\w.]+) .*?\bms=(\d+)")


def collect(days=14, path=LOG):
    """[(version, ms, datetime)] within the window, oldest first."""
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for p in (path, path + ".1"):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    m = LINE.match(ln)
                    if not m:
                        continue
                    when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    if when >= cutoff:
                        out.append((m.group(2), int(m.group(3)), when))
        except OSError:
            continue
    return sorted(out, key=lambda r: r[2])


def summarize(rows):
    """{version: stats} with n/min/avg/max/p95 (empty dict if no data)."""
    by_ver = {}
    for ver, ms, _when in rows:
        by_ver.setdefault(ver, []).append(ms)
    stats = {}
    for ver, ms in sorted(by_ver.items(), key=lambda kv: kv[0]):
        ordered = sorted(ms)
        p95 = ordered[max(0, int(round(0.95 * len(ordered))) - 1)]
        stats[ver] = {
            "n": len(ms),
            "min": min(ms),
            "avg": round(statistics.mean(ms)),
            "max": max(ms),
            "p95": p95,
        }
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14,
                    help="window size in days (default 14)")
    ap.add_argument("--json", action="store_true",
                    help="print stats as JSON instead of a table")
    args = ap.parse_args(argv)

    rows = collect(days=args.days)
    stats = summarize(rows)
    if args.json:
        print(json.dumps(stats, indent=2))
        return 0

    print(f"startup latency trend (last {args.days} days)")
    if not stats:
        print("  no startup events with ms= yet (v1.25.8+ writes them)")
        return 0
    print(f"{'version':<10}{'n':>5}{'min':>8}{'avg':>8}{'p95':>8}{'max':>8}")
    for ver, s in stats.items():
        print(f"{ver:<10}{s['n']:>5}{s['min']:>8}{s['avg']:>8}"
              f"{s['p95']:>8}{s['max']:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
