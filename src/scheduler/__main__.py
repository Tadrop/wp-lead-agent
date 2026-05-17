"""Run the follow-up scheduler.

Use a single tick (for cron) or a daemon loop:

    python -m src.scheduler              # daemon, 1-hour tick
    python -m src.scheduler --once       # single tick, for cron
"""

from __future__ import annotations

import argparse
import time

from ..logging_setup import configure_logging, get_logger
from ..settings import get_settings
from .followup import FollowupScheduler

log = get_logger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run a single tick and exit (for cron).")
    ap.add_argument("--interval", type=int, default=3600, help="Seconds between ticks in daemon mode.")
    args = ap.parse_args()

    configure_logging(get_settings().log_level)
    scheduler = FollowupScheduler()

    if args.once:
        scheduler.tick()
        return 0

    while True:
        scheduler.tick()
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
