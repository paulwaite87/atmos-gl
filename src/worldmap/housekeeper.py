#!/usr/bin/env python3
"""Housekeeper: deletes expired layer cache files.

Every cache file is minted with a uniform '<layer>_cache_' marker by
Updater.cache_path(), so this process can find and expire caches by that single
marker — no per-layer pattern lists to maintain. Live render outputs never carry
the marker, so they are safe from deletion by construction rather than by a guard
list. The owning layer is parsed straight from the prefix, and that layer's
'cache_expiry_days' decides the cutoff (0 or missing means keep forever).
"""

import os
import sys
import time
import logging
import argparse

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.logging import setup_logging, set_loglevel

logger = logging.getLogger("worldmap.housekeeper")

CACHE_MARKER = "_cache_"
HEARTBEAT_SECONDS = 3600  # wake hourly; actual work cadence is days_between_runs


class Housekeeper:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = WorldMapConfig(config_path)
        self.settings = {}
        self.refresh_settings()
        logger.debug("Initializing Housekeeper")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("housekeeper")
        log_level = self.settings.get("log_level", None)
        if log_level:
            set_loglevel(log_level)

    def _interval_seconds(self) -> float:
        try:
            days = int(self.settings.get("days_between_runs", 1))
        except (TypeError, ValueError):
            days = 1
        days = min(14, max(1, days))
        return days * 86400.0

    def _data_dir(self) -> str:
        workdir = self.config.get_setting("common", "workdir", ".")
        return os.path.join(workdir, "data")

    def _expiry_days_for(self, layer: str) -> float:
        try:
            return float(self.config.get_setting(layer, "cache_expiry_days", 0))
        except (TypeError, ValueError):
            return 0.0

    def prune_image_files(self, pattern: str = "*.png", expiry_hours: int = 48):
        """Delete image files older than expiry_hours."""
        import glob
        from datetime import datetime, timedelta, timezone

        workdir = self.config.get_setting("common", "workdir", ".")
        data_dir = os.path.join(workdir, "data")

        if not os.path.isdir(data_dir):
            return

        now = datetime.now(timezone.utc)
        expiry_delta = timedelta(hours=expiry_hours)
        cutoff = now - expiry_delta

        deleted_count = 0
        for filepath in glob.glob(os.path.join(data_dir, pattern)):
            basename = os.path.basename(filepath)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=timezone.utc)
                if mtime < cutoff:
                    os.remove(filepath)
                    deleted_count += 1
                    logger.debug(f"Pruned per-hour output: {basename}")
            except OSError as e:
                logger.warning(f"Failed to prune {filepath}: {e}")

        if deleted_count > 0:
            logger.info(f"Housekeeper pruned {deleted_count} per-hour output file(s) older than {expiry_hours}h.")

    def sweep(self):
        data_dir = self._data_dir()
        if not os.path.isdir(data_dir):
            logger.warning(f"Data dir {data_dir} not found; nothing to sweep.")
            return

        dry_run = bool(self.settings.get("dry_run", False))
        now = time.time()
        examined = deleted = 0
        freed_bytes = 0

        # os.scandir is a single, non-recursive listing confined to the data dir;
        # combined with the CACHE_MARKER test this cannot reach any other file.
        for entry in os.scandir(data_dir):
            try:
                if not entry.is_file():
                    continue
                name = entry.name
                if CACHE_MARKER not in name:
                    continue  # not a cache file -> never eligible (outputs are safe)

                layer = name.split(CACHE_MARKER, 1)[0]
                expiry_days = self._expiry_days_for(layer)
                if expiry_days <= 0:
                    continue  # 0 / missing -> keep forever for this layer

                examined += 1
                stat = entry.stat()
                age_days = (now - stat.st_mtime) / 86400.0
                if age_days < expiry_days:
                    continue

                if dry_run:
                    logger.info(
                        f"[dry-run] would delete {name} "
                        f"(layer={layer}, age={age_days:.1f}d >= {expiry_days:g}d, "
                        f"{stat.st_size} bytes)"
                    )
                else:
                    os.remove(entry.path)
                    logger.info(f"deleted {name} (layer={layer}, age={age_days:.1f}d)")
                deleted += 1
                freed_bytes += stat.st_size
            except FileNotFoundError:
                continue  # raced with a regenerating task; fine
            except OSError as exc:
                logger.warning(f"Could not process {entry.name}: {exc}")

        prefix = "[dry-run] " if dry_run else ""
        logger.info(
            f"Housekeeper sweep complete: {prefix}{deleted} file(s) "
            f"({freed_bytes / 1_000_000:.1f} MB) of {examined} expirable; "
            f"data dir {data_dir}"
        )

    def run(self):
        last_run = None
        logger.info("Housekeeper service started.")
        while True:
            self.refresh_settings()
            if self.settings.get("enabled", False):
                now = time.time()
                interval = self._interval_seconds()
                if last_run is None or (now - last_run) >= interval:
                    logger.info("Housekeeper run started.")
                    self.sweep()
                    self.prune_image_files()
                    last_run = now
            else:
                logger.debug("Housekeeper disabled; skipping.")
            time.sleep(HEARTBEAT_SECONDS)


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="WorldMap cache Housekeeper")
    parser.add_argument("--config", required=True, help="Path to worldmap.json")
    args = parser.parse_args()

    try:
        Housekeeper(args.config).run()
    except KeyboardInterrupt:
        logger.info("Housekeeper gracefully stopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()
