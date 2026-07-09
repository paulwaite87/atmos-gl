#!/usr/bin/env python3
"""Markers backend task: samples weather onto the DB 'markers' table.

The geojson->DB sync of marker definitions is owned by data_collector (the markers_sync
collector); this task is now weather-only:
  - Weather — when markers.weather_popup is on, sample the GFS fields we already ingest
    (temperature TMP@2m, wind UGRD/VGRD@10m, humidity RH@2m) at the run hour valid "now",
    bilinearly at each place marker, and write the wx_* columns.

It reads the place list straight from the canonical geojson (load_marker_rows) so the ids
match exactly the rows markers_sync upserts, and updates only the wx_* columns.

No external weather API and no JSON file: the frontend reads everything (static markers +
current weather) from /api/markers/geojson. Sampled values are the GFS model valid-now —
good enough for a city weather popup, free, and refreshed each cycle.
"""
import logging
from datetime import datetime, timezone

import numpy as np

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.db.marker_adapter import MarkerAdapter
from atmos_gl.collectors.markers_sync import load_marker_rows
from .common import Updater, MapData, ForecastState

logger = logging.getLogger(__name__)


class MarkerUpdater(Updater):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        # Reads the existing "markers" config section (shared with the frontend markers
        # layer); driven by its weather_popup flag and runs_per_day.
        super().__init__(config, "Markers", map_data)
        self.input_path = self.settings.get("infile", "markers/markers.geojson")
        self.marker_adapter = MarkerAdapter()

    # ---- field sampling -----------------------------------------------------
    @staticmethod
    def _sample(field, arr, lats_q, lons_q):
        """Bilinearly sample a 2-D field `arr` (laid out on field['lat'] x field['lon'])
        at the query lat/lon points. Returns a 1-D array (NaN outside coverage) or None
        if the field/array is missing. Handles either latitude order."""
        if field is None or arr is None:
            return None
        lat = field.get("lat")
        lon = field.get("lon")
        if lat is None or lon is None:
            return None
        from scipy.interpolate import RegularGridInterpolator

        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        vals = np.asarray(arr, dtype=np.float64)
        # RegularGridInterpolator requires strictly increasing axes. Longitudes come in
        # ascending from _standardize_lon; latitudes may be north-first (descending), so
        # flip the axis AND the rows together if needed.
        if lat.size >= 2 and lat[0] > lat[-1]:
            lat = lat[::-1]
            vals = vals[::-1, :]
        try:
            rgi = RegularGridInterpolator(
                (lat, lon), vals, bounds_error=False, fill_value=np.nan
            )
            return rgi(np.column_stack([lats_q, lons_q]))
        except Exception as e:
            logger.warning(f"Markers: sampling failed: {e}")
            return None

    def _resolve_run_hour(self):
        """Resolve the freshest run + the available hour valid 'now' DIRECTLY from the
        fieldstore catalog, NOT from the cached GFS baseline (which drifts stale in a
        long-lived process). Scoped to temperature+wind (always ingested together) so an
        unrelated cycle (RTOFS currents) can't outrank the GFS run and humidity — which
        can lag a cycle — doesn't gate resolution. Returns (run_date, run_id, fhour)|None.
        """
        try:
            store = self._store
            avail = store.field_catalog_adapter.get_latest_run_hours(
                products=["temperature", "wind"]
            )
            logger.debug(f"_resolve_run_hour returned: {avail}")
        except Exception as e:
            logger.warning(f"Markers: catalog lookup failed: {e}")
            return None
        if not avail or not avail.get("hours"):
            return None
        run_date, run_id, hours = avail["run_date"], avail["run_id"], avail["hours"]
        logger.debug(f"Details: run_date: {run_date} run_id: {run_id} hours: {hours}")
        try:
            run_datetime = datetime(run_date.year, run_date.month, run_date.day)
            run_start = run_datetime.replace(hour=int(run_id), tzinfo=timezone.utc)
            logger.debug(f"run_start: {run_start}")
        except Exception as e:
            logger.debug(f"failed run_start: {run_date} {e}")
            return None
        target = (datetime.now(timezone.utc) - run_start).total_seconds() / 3600.0
        fhour = min(hours, key=lambda h: abs(int(h) - target))
        return run_date, run_id, int(fhour)

    # ---- main ---------------------------------------------------------------
    def run(self, max_hours=None):
        # max_hours is a no-op here -- markers render once per cycle, not per forecast
        # hour, so it has nothing to cap. Accepted only so layer_builder's dispatch can
        # call every TASK_CLASSES entry's run() the same way.
        resolved = self._resolve_run_hour()
        if not resolved:
            logger.warning(
                "Markers: no temperature/wind fields in the store yet; skipping weather "
                "sample this cycle (data collector may not have ingested this run)."
            )
            return
        run_date, run_id, fhour = resolved
        state = ForecastState.at_hour(run_date, run_id, fhour)

        # Only 'place' markers get weather; reuse the importer's ids so the UPDATE matches
        # exactly the rows it upserted.
        try:
            places = [r for r in load_marker_rows() if r["kind"] == "place"]
        except Exception as e:
            logger.error(f"Markers: could not read markers file for sampling: {e}")
            return
        if not places:
            return

        lats_q = np.array([p["lat"] for p in places], dtype=np.float64)
        lons_q = np.array([p["lon"] for p in places], dtype=np.float64)

        temp_f = self.get_db_field_at_hour(state, "temperature")
        wind_f = self.get_db_field_at_hour(state, "wind")
        rh_f = self.get_db_field_at_hour(state, "humidity")
        if temp_f is None and wind_f is None and rh_f is None:
            logger.warning(
                f"Markers: fields vanished for {run_date} {run_id}Z f{fhour:03d} "
                "between catalog lookup and read; skipping weather sample."
            )
            return

        t = self._sample(temp_f, temp_f.get("values") if temp_f else None, lats_q, lons_q)
        rh = self._sample(rh_f, rh_f.get("values") if rh_f else None, lats_q, lons_q)
        u = self._sample(wind_f, wind_f.get("u") if wind_f else None, lats_q, lons_q)
        v = self._sample(wind_f, wind_f.get("v") if wind_f else None, lats_q, lons_q)

        ws = wd = None
        if u is not None and v is not None:
            ws = np.hypot(u, v)  # m/s
            # Meteorological direction the wind blows FROM (deg, 0=N, 90=E).
            wd = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0

        # Valid time from whichever field provided one.
        valid_iso = None
        for f in (temp_f, wind_f, rh_f):
            if f and f.get("valid_time"):
                vt = f["valid_time"]
                valid_iso = vt.isoformat() if hasattr(vt, "isoformat") else str(vt)
                break

        updates = []
        for i, p in enumerate(places):
            ti = float(t[i]) if (t is not None and np.isfinite(t[i])) else None
            rhi = float(rh[i]) if (rh is not None and np.isfinite(rh[i])) else None
            wsi = float(ws[i]) if (ws is not None and np.isfinite(ws[i])) else None
            wdi = float(wd[i]) if (wd is not None and np.isfinite(wd[i])) else None
            if ti is None and rhi is None and wsi is None and wdi is None:
                continue
            updates.append(
                {
                    "id": p["id"],
                    "t": round(ti, 1) if ti is not None else None,
                    "rh": int(round(rhi)) if rhi is not None else None,
                    "ws": round(wsi, 1) if wsi is not None else None,
                    "wd": int(round(wdi)) % 360 if wdi is not None else None,
                    "valid_time": valid_iso,
                }
            )

        if updates:
            self.marker_adapter.update_marker_weather(updates)
        logger.info(
            f"Markers: weather updated for {len(updates)}/{len(places)} place markers "
            f"(f{fhour:03d})."
        )