#!/usr/bin/env python3
"""
fieldstore.py — Separate catalog (Postgres) from bulk field storage (files).

Fields (lat, lon, values, values2, u, v) are stored as compressed .npz files
on the shared volume. The database maintains only a thin metadata catalog
(shape, valid_time, storage_uri) for fast lookups and pruning.

This decouples the bulk bytes (filesystem) from transactional metadata (DB),
simplifying both and avoiding array serialization overhead.
"""

import os
import logging
import tempfile
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)


class FieldStore:
    """Manage field storage across catalog (DB) and files."""

    def __init__(self, db, workdir: str = "."):
        """
        Args:
            db: Database instance (handles catalog rows)
            workdir: Root directory for field storage (e.g., /data/worldmap)
        """
        self.db = db
        self.workdir = Path(workdir)
        self.fields_dir = self.workdir / "data" / "fields"
        self.fields_dir.mkdir(parents=True, exist_ok=True)

    def field_path(self, gfs_date: str, gfs_run: str, fhour: int, product: str) -> Path:
        """Compute the storage path for a field.

        Pattern: {workdir}/fields/{date}/{run}/{product}_f{fhour:03d}.npz

        Example: /data/worldmap/fields/20260612/00/precipitation_f003.npz
        """
        return self.fields_dir / gfs_date / gfs_run / f"{product}_f{int(fhour):03d}.npz"

    def store_field(
        self,
        gfs_date: str,
        gfs_run: str,
        fhour: int,
        product: str,
        unpacked: dict,
        valid_time=None,
    ) -> bool:
        """Write a field to disk and catalog it.

        Args:
            gfs_date, gfs_run, fhour, product: Field key
            unpacked: Dict from unpack module {lat, lon, values, values2, u, v}
            valid_time: Forecast valid time (optional)

        Returns:
            True if successful, False otherwise
        """
        fhour = int(fhour)
        path = self.field_path(gfs_date, gfs_run, fhour, product)

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Prepare arrays
        lat = np.asarray(unpacked["lat"], dtype=np.float64)
        lon = np.asarray(unpacked["lon"], dtype=np.float64)
        nlat, nlon = int(lat.shape[0]), int(lon.shape[0])

        # Build the array dict: only include non-None fields
        arrays = {
            "lat": lat,
            "lon": lon,
        }
        for key in ("values", "values2", "u", "v"):
            arr = unpacked.get(key)
            if arr is not None:
                arrays[key] = np.asarray(arr, dtype=np.float32)

        # Write atomically: write to temp file, then move into place
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, suffix=".npz", delete=False
            ) as tmp:
                tmp_path = tmp.name

            np.savez_compressed(tmp_path, **arrays)
            os.replace(tmp_path, path)
            logger.debug(f"Stored field to {path}")
        except Exception as e:
            logger.error(f"Error writing field {path}: {e}")
            os.unlink(tmp_path)
            return False

        # Upsert the catalog row (metadata only)
        try:
            rel_path = path.relative_to(self.workdir)
            self.db.upsert_field_catalog(
                gfs_date=gfs_date,
                gfs_run=gfs_run,
                fhour=fhour,
                product=product,
                nlat=nlat,
                nlon=nlon,
                valid_time=valid_time,
                storage_uri=str(rel_path),
            )
            logger.debug(
                f"Catalogued field {gfs_date}/{gfs_run}/f{fhour:03d}/{product}"
            )
            return True
        except Exception as e:
            logger.error(f"Error cataloguing field {product}: {e}")
            # File is written but catalog failed — housekeeper will reconcile
            return False

    def get_field(
        self, gfs_date: str, gfs_run: str, fhour: int, product: str
    ) -> dict | None:
        """Fetch a field from disk.

        Returns:
            Dict {lat, lon, values|None, values2|None, u|None, v|None, valid_time}
            or None if not found or on error.
        """
        fhour = int(fhour)

        # Check catalog first (fast, indexed)
        try:
            catalog_row = self.db.get_field_catalog(gfs_date, gfs_run, fhour, product)
            if not catalog_row:
                return None
        except Exception as e:
            logger.debug(f"Error querying catalog: {e}")
            return None

        # Compute path from catalog
        path = self.workdir / catalog_row["storage_uri"]

        # Verify file exists (catalog/file divergence check)
        if not path.exists():
            logger.warning(
                f"Catalog row exists but file missing: {path} "
                f"(catalog/file divergence; treating as cache miss)"
            )
            return None

        # Load the file. np.load on a .npz returns an NpzFile that keeps the
        # underlying handle open until closed, so use it as a context manager
        # and force each needed array into memory before the handle is released.
        try:
            with np.load(path) as data:
                result = {
                    "lat": np.asarray(data["lat"], dtype=np.float64),
                    "lon": np.asarray(data["lon"], dtype=np.float64),
                    "valid_time": catalog_row["valid_time"],
                }
                # Include non-None fields
                for key in ("values", "values2", "u", "v"):
                    if key in data:
                        result[key] = np.asarray(data[key], dtype=np.float32)
                    else:
                        result[key] = None
            return result
        except Exception as e:
            logger.error(f"Error loading field {path}: {e}")
            return None

    def field_exists(
        self, gfs_date: str, gfs_run: str, fhour: int, product: str
    ) -> bool:
        """Check if a field exists in the catalog (fast, no file I/O)."""
        try:
            return self.db.field_catalog_exists(gfs_date, gfs_run, int(fhour), product)
        except Exception as e:
            logger.debug(f"Error checking field existence: {e}")
            return False

    def get_field_meta(
        self, gfs_date: str, gfs_run: str, fhour: int, product: str
    ) -> dict | None:
        """Fetch only the catalog metadata for a field (no array file load).

        Returns the catalog row dict (nlat, nlon, valid_time, storage_uri, ...)
        or None if not catalogued. Used by freshness checks that only need
        valid_time, avoiding a full .npz read.
        """
        try:
            return self.db.get_field_catalog(gfs_date, gfs_run, int(fhour), product)
        except Exception as e:
            logger.debug(f"Error fetching field meta: {e}")
            return None

    def delete_field(
        self, gfs_date: str, gfs_run: str, fhour: int, product: str
    ) -> bool:
        """Delete a field from both catalog and disk."""
        fhour = int(fhour)
        path = self.field_path(gfs_date, gfs_run, fhour, product)

        # Delete from catalog
        try:
            self.db.delete_field_catalog(gfs_date, gfs_run, fhour, product)
        except Exception as e:
            logger.warning(f"Error deleting from catalog: {e}")

        # Delete file
        try:
            if path.exists():
                os.unlink(path)
                logger.debug(f"Deleted field file: {path}")
                return True
        except Exception as e:
            logger.error(f"Error deleting file {path}: {e}")
            return False

        return True

    def live_product_hours(self):
        """Set of all (product, fhour) pairs present anywhere in the catalog.

        Passthrough to db.get_live_product_hours; used by the housekeeper to detect
        orphaned per-hour render files (files whose layer+hour no longer has any
        catalog backing after a run advanced).
        """
        return self.db.get_live_product_hours()

    def reconcile(self):
        """Find and fix catalog/file divergence.

        Deletes:
        - Rows with missing files
        - Orphan files with missing rows (optional, to reclaim space)
        """
        logger.info("Starting fieldstore reconciliation...")

        # Scan catalog for missing files
        try:
            orphan_rows = self.db.get_orphan_field_rows(self.workdir)
            for row in orphan_rows:
                gfs_date, gfs_run, fhour, product = (
                    row["gfs_date"],
                    row["gfs_run"],
                    row["fhour"],
                    row["product"],
                )
                logger.warning(
                    f"Removing orphan catalog row: {gfs_date}/{gfs_run}/f{fhour:03d}/{product} "
                    f"(file missing)"
                )
                self.db.delete_field_catalog(gfs_date, gfs_run, fhour, product)
        except Exception as e:
            logger.error(f"Error scanning for orphan rows: {e}")

        logger.info("Fieldstore reconciliation complete.")

    def prune_except_run(self, gfs_date: str, gfs_run: str, products=None):
        """Delete catalogued fields NOT belonging to (gfs_date, gfs_run).

        Used by collectors after a successful refresh to drop superseded runs. When
        `products` is given (an iterable of product names), only fields in that set
        are eligible for pruning — so a collector prunes ONLY its own product family
        and can't delete another datasource's fields (GFS runs and RTOFS currents
        live under different (date, run) keys in the same catalog). When `products`
        is None the old behaviour (prune everything not matching) is retained.
        """
        try:
            rows = self.db.get_field_rows_except(gfs_date, gfs_run, products=products)
        except Exception as e:
            logger.debug(f"prune_except_run: catalog query failed: {e}")
            return

        product_filter = set(products) if products is not None else None
        removed = 0
        for row in rows:
            d, r, fh, p = row["gfs_date"], row["gfs_run"], row["fhour"], row["product"]
            if product_filter is not None and p not in product_filter:
                continue  # not our product family; leave it for its own collector
            # Delete the file (path comes from the catalog's storage_uri)
            uri = row.get("storage_uri")
            if uri:
                fpath = self.workdir / uri
                try:
                    if fpath.exists():
                        os.unlink(fpath)
                except OSError as e:
                    logger.debug(f"prune_except_run: unlink {fpath} failed: {e}")
            try:
                self.db.delete_field_catalog(d, r, fh, p)
                removed += 1
            except Exception as e:
                logger.debug(f"prune_except_run: row delete failed: {e}")

        if removed:
            logger.info(
                f"Fieldstore pruned {removed} superseded field(s) "
                f"(kept {gfs_date}/{gfs_run})."
            )

    def prune_expired(self, expiry_hours: int = 48) -> int:
        """Delete catalogued fields older than expiry_hours (row + file).
        Returns the number of fields removed. Used by the housekeeper.
        """
        try:
            rows = self.db.get_expired_field_rows(expiry_hours)
        except Exception as e:
            logger.debug(f"prune_expired: catalog query failed: {e}")
            return 0

        removed = 0
        for row in rows:
            d, r, fh, p = row["gfs_date"], row["gfs_run"], row["fhour"], row["product"]
            uri = row.get("storage_uri")
            if uri:
                fpath = self.workdir / uri
                try:
                    if fpath.exists():
                        os.unlink(fpath)
                except OSError as e:
                    logger.debug(f"prune_expired: unlink {fpath} failed: {e}")
            try:
                self.db.delete_field_catalog(d, r, fh, p)
                removed += 1
            except Exception as e:
                logger.debug(f"prune_expired: row delete failed: {e}")
        return removed

    def get_size_on_disk(self) -> int:
        """Return total bytes used by field files (for monitoring)."""
        total = 0
        if self.fields_dir.exists():
            for root, dirs, files in os.walk(self.fields_dir):
                for f in files:
                    if f.endswith(".npz"):
                        total += os.path.getsize(os.path.join(root, f))
        return total


# ---------------------------------------------------------------------------
# Module-level singleton factory
#
# Tasks/collector/housekeeper call get_store(workdir) to obtain a shared
# FieldStore. The store owns its own Database handle, matching the existing
# pattern where components construct Database() as needed. The instance is
# cached per process so repeated calls are cheap.
# ---------------------------------------------------------------------------

_store_instance = None


def get_store(workdir: str = ".", db=None):
    """Return a process-wide FieldStore, creating it on first use.

    Args:
        workdir: Root directory for field storage (e.g. the common.workdir).
        db: Optional Database instance to reuse. If omitted, one is created.

    The first call fixes the workdir/db for the process. Subsequent calls
    return the same instance and ignore their arguments.
    """
    global _store_instance
    if _store_instance is None:
        if db is None:
            from worldmap.lib.db import Database

            db = Database()
        _store_instance = FieldStore(db, workdir)
    return _store_instance


def init_fieldstore(db, workdir: str = ".") -> FieldStore:
    """Explicitly (re)initialise the global fieldstore instance.

    Useful at process startup when you already hold a Database handle and want
    the store bound to it. Overwrites any previously created singleton.
    """
    global _store_instance
    _store_instance = FieldStore(db, workdir)
    return _store_instance
