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
import re
import sys
import glob
import time
import logging
import argparse

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.logging import setup_logging, set_loglevel
from atmos_gl.lib import fieldstore
from atmos_gl.lib.scheduling import interval_elapsed
from atmos_gl.db.ship_adapter import ShipAdapter

logger = logging.getLogger("atmos_gl.housekeeper")

CACHE_MARKER = "_cache_"
HEARTBEAT_SECONDS = 3600  # wake hourly; actual work cadence is days_between_runs

# '{layer}_f{NNN}{suffix}' where suffix is .png, _data.png, _labels.geojson, etc.
PER_HOUR_PATTERN = re.compile(r"^(?P<layer>[A-Za-z0-9]+)_f(?P<hour>\d{3})(?P<suffix>[._].+)?$")


def _parse_hour_output_name(name: str) -> tuple[str, int] | None:
    """Parse a per-hour render output's filename into (layer, fhour), or None if it
    doesn't match the per-hour naming convention at all."""
    m = PER_HOUR_PATTERN.match(name)
    if not m:
        return None
    return m.group("layer"), int(m.group("hour"))


def _is_orphaned(layer: str, fhour: int, known_products: set, live: set) -> bool:
    """A per-hour output is orphaned iff its layer is a managed catalog product (so an
    unrelated file from an unmanaged source is never touched) AND its (layer, fhour)
    is absent from the catalog's full set of live (product, fhour) pairs. Matching
    across every run (not just the latest) means a file backed by any live row is
    kept -- safe during run transitions.
    """
    if layer not in known_products:
        return False
    return (layer, fhour) not in live


class Housekeeper:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = AtmosGLConfig(config_path)
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
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(filepath), tz=timezone.utc
                )
                if mtime < cutoff:
                    os.remove(filepath)
                    deleted_count += 1
                    logger.debug(f"Pruned per-hour output: {basename}")
            except OSError as e:
                logger.warning(f"Failed to prune {filepath}: {e}")

        if deleted_count > 0:
            logger.info(
                f"Housekeeper pruned {deleted_count} per-hour output file(s) older than {expiry_hours}h."
            )

    def prune_fields(self, expiry_hours: int = 48):
        """Prune expired fieldstore entries (catalog row + .npz file) and
        reconcile any catalog/file divergence.

        The data_collector already drops superseded *runs* each cycle; this is the
        safety net that expires anything older than expiry_hours and clears orphan
        rows left by interrupted writes.
        """
        workdir = self.config.get_setting("common", "workdir", ".")
        try:
            store = fieldstore.get_store(workdir)
        except Exception as e:
            logger.warning(f"Housekeeper: could not open fieldstore: {e}")
            return

        # Remove catalog rows whose files have vanished (and vice-versa).
        try:
            store.reconcile()
        except Exception as e:
            logger.warning(f"Housekeeper: fieldstore reconcile failed: {e}")

        # Expire old fields (row + file).
        try:
            removed = store.prune_expired(expiry_hours=expiry_hours)
            if removed:
                logger.info(
                    f"Housekeeper pruned {removed} fieldstore field(s) older than {expiry_hours}h."
                )
        except Exception as e:
            logger.warning(f"Housekeeper: fieldstore prune failed: {e}")

    def prune_vessel_tracks(self, expiry_days: float):
        """Prune ship_position rows older than expiry_days (0/missing -> keep forever).

        update_ship_position_data writes one row per AIS position update with nothing
        else capping table growth, unlike the file-based caches this class already
        prunes.
        """
        if not expiry_days or expiry_days <= 0:
            return
        try:
            deleted = ShipAdapter().prune_vessel_tracks(expiry_days)
            if deleted:
                logger.info(
                    f"Housekeeper pruned {deleted} vessel position record(s) "
                    f"older than {expiry_days:g}d."
                )
        except Exception as e:
            logger.warning(f"Housekeeper: vessel track prune failed: {e}")

    def prune_orphaned_hour_outputs(self):
        """Delete per-hour render outputs whose (layer, hour) no longer has any
        backing field in the catalog.

        Per-hour render files are named '{layer}_f{NNN}{suffix}' (e.g.
        currents_f019_data.png, precipitation_f003.png, isobars_f012_labels.geojson).
        When a forecast run advances, the live forecast window shifts and the old
        hours' fields are pruned from the fieldstore — but their rendered PNG/GeoJSON
        outputs linger on disk. The data_collector's prune handles the .npz/catalog
        side; this is the matching cleanup for the rendered outputs, for ALL layers.

        The orphan decision itself (_parse_hour_output_name + _is_orphaned, both
        module-level and pure) is the testable surface; this method is the thin I/O
        shell around it — glob the data dir, ask the predicate, delete or dry-run log.
        Base outputs ('currents.png', 'currents_key.png') have no '_f{NNN}' segment
        and are never matched, so they are safe by construction.
        """
        data_dir = self._data_dir()
        if not os.path.isdir(data_dir):
            return

        workdir = self.config.get_setting("common", "workdir", ".")
        try:
            store = fieldstore.get_store(workdir)
            live = store.live_product_hours()  # set of (product, fhour)
        except Exception as e:
            logger.warning(
                f"Housekeeper: could not read live hours; skipping orphan-output sweep: {e}"
            )
            return

        # Products the catalog knows about — only these are eligible for deletion, so
        # a stray file from an unmanaged source is never removed.
        known_products = {p for (p, _h) in live}
        if not known_products:
            logger.debug("Housekeeper: catalog empty; skipping orphan-output sweep.")
            return

        dry_run = bool(self.settings.get("dry_run", False))

        deleted = 0
        for filepath in glob.glob(os.path.join(data_dir, "*_f[0-9][0-9][0-9]*")):
            name = os.path.basename(filepath)
            parsed = _parse_hour_output_name(name)
            if parsed is None:
                continue
            layer, fhour = parsed
            if not _is_orphaned(layer, fhour, known_products, live):
                continue
            # Orphaned: a per-hour output for a managed layer with no catalog backing.
            try:
                if dry_run:
                    logger.info(
                        f"[dry-run] would delete orphaned output {name} (layer={layer}, f{fhour:03d})"
                    )
                else:
                    os.remove(filepath)
                    logger.info(
                        f"Pruned orphaned per-hour output {name} (layer={layer}, f{fhour:03d})"
                    )
                deleted += 1
            except FileNotFoundError:
                continue  # raced with a regenerating task; fine
            except OSError as e:
                logger.warning(f"Failed to prune orphaned output {name}: {e}")

        if deleted:
            prefix = "[dry-run] " if dry_run else ""
            logger.info(
                f"Housekeeper {prefix}pruned {deleted} orphaned per-hour output(s)."
            )

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
                if interval_elapsed(last_run, now, interval):
                    logger.info("Housekeeper run started.")
                    self.sweep()
                    self.prune_image_files()
                    # Per-hour isobar label GeoJSONs age out like the PNG outputs.
                    self.prune_image_files(pattern="*.geojson")
                    field_expiry_h = int(self.settings.get("field_expiry_hours", 48))
                    self.prune_fields(expiry_hours=field_expiry_h)
                    # After the catalog is reconciled/pruned, drop rendered per-hour
                    # outputs (all layers) whose (layer, hour) no longer has a field.
                    self.prune_orphaned_hour_outputs()
                    vessel_expiry_d = float(
                        self.config.get_setting(
                            "shipping_collector", "vessel_track_expiry_days", 0
                        )
                    )
                    self.prune_vessel_tracks(vessel_expiry_d)
                    last_run = now
            else:
                logger.debug("Housekeeper disabled; skipping.")
            time.sleep(HEARTBEAT_SECONDS)


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Atmos GL cache Housekeeper")
    parser.add_argument("--config", required=True, help="Path to atmos-gl.json")
    args = parser.parse_args()

    try:
        Housekeeper(args.config).run()
    except KeyboardInterrupt:
        logger.info("Housekeeper gracefully stopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()
