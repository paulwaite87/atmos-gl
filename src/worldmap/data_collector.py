#!/usr/bin/env python3
"""Backward-compat shim for the data collector.

The collector was refactored out of one monolithic class into focused collaborators under
worldmap.collectors:

  * CollectorService  (collectors/service.py)     — orchestration: run loop, cadences,
                                                     embedded async supervision.
  * FieldIngest       (collectors/field_ingest.py) — GFS/RTOFS field ingestion + backfill.
  * file-cache + event-feed collectors             — collectors/{sst,clouds,quakes,...}.py

This module remains only so existing entry points keep working unchanged:
  * the Docker command  `python -m worldmap.data_collector --config <path>`
  * the console script   datacollector = "worldmap.data_collector:main"

Both now run CollectorService. `DataCollector` is kept as an alias for any code that still
imports the old name. Prefer importing CollectorService directly in new code; this shim can
be removed once the Docker command / entry point are pointed at worldmap.collectors.service.
"""
from worldmap.collectors.service import CollectorService, main

# Legacy alias: `from worldmap.data_collector import DataCollector` still resolves.
DataCollector = CollectorService

__all__ = ["CollectorService", "DataCollector", "main"]


if __name__ == "__main__":
    main()
