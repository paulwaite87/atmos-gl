#!/usr/bin/env python3
"""Run one event-feed or file-cache collector right now, outside CollectorService's
schedule -- for when you're waiting on a collector to test something. Invoked inside
the running data_collector container by `make collect name=<section> [force=1]`, so it
uses the deployed code, config and database.

Same steps as a scheduled run (collectors/driving.py's EventFeedDriver): respects
data_collector.channel_enabled and has_new_data(), records start/success/failure in
process_status so the Data Status page reflects it -- but skips is_stale(), which is
the point. force=1 also bypasses channel_enabled and has_new_data(), to re-collect
unchanged data. Holds the collector's advisory lock (db/collector_lock.py) while it
runs: it waits for an in-progress scheduled run of the same collector, and the
schedule skips the collector while this holds it. Field collectors (GFS/RTOFS) aren't
covered -- they already run unconditionally every cycle, gap-filling per hour.
"""
import argparse
import logging
import sys

from atmos_gl.collectors import CACHE_COLLECTORS, COLLECTORS
from atmos_gl.db.process_status_adapter import ProcessStatusAdapter

logger = logging.getLogger("atmos_gl.collectors.run_once")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def runnable_collectors() -> dict:
    """section -> collector class, for every collector run_once can drive."""
    return {cls.section: cls for cls in (*COLLECTORS, *CACHE_COLLECTORS)}


def run_once(config, name, force=False, lock=None, process_status_adapter=None) -> int:
    """Runs collector `name` (its section) once. Returns an exit code."""
    collectors = runnable_collectors()
    cls = collectors.get(name)
    if cls is None:
        print(f"Unknown collector {name!r}. Choose one of: {', '.join(sorted(collectors))}")
        return EXIT_USAGE

    channel_enabled = config.get_setting("data_collector", "channel_enabled", {}) or {}
    if cls.channel_key and not channel_enabled.get(cls.channel_key, True) and not force:
        print(f"{name}: channel {cls.channel_key!r} is disabled on the Data Status page; "
              f"use force=1 to run it anyway.")
        return EXIT_FAILED

    if lock is None:
        from atmos_gl.db.collector_lock import collector_lock as lock
    status = process_status_adapter or ProcessStatusAdapter()

    def say_waiting():
        print(f"{name}: another run (scheduled or manual) is in progress; "
              f"waiting for it to finish...", flush=True)

    with lock(name, wait=True, on_wait=say_waiting):
        collector = cls(config)
        if not force and not collector.has_new_data():
            print(f"{name}: no new data upstream; nothing to do (use force=1 to collect anyway).")
            return EXIT_OK
        print(f"{name}: collecting...", flush=True)
        status.record_process_start(name, "collector")
        try:
            collector.collect()
        except Exception as e:
            logger.exception(f"{name}: manual run failed")
            status.record_process_run(name, "collector", success=False, error=str(e))
            print(f"{name}: failed: {e}")
            return EXIT_FAILED
        status.record_process_run(name, "collector", success=True)
    print(f"{name}: done.")
    return EXIT_OK


def main():
    from atmos_gl.lib.config import AtmosGLConfig
    from atmos_gl.lib.logging import setup_logging

    parser = argparse.ArgumentParser(description="Run one collector now, outside its schedule.")
    parser.add_argument("--config", required=True, help="Path to atmos-gl.json")
    parser.add_argument("name", help="Collector section, e.g. world_events")
    parser.add_argument("--force", action="store_true",
                        help="Collect even if unchanged upstream or its channel is disabled")
    args = parser.parse_args()

    setup_logging()
    config = AtmosGLConfig(args.config)
    config.load()
    sys.exit(run_once(config, args.name, force=args.force))


if __name__ == "__main__":
    main()
