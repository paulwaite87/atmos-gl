#!/usr/bin/env python3
"""Markers backend task: enriches the place-marker layer with current weather for popups.

Rather than calling an external per-city weather API (thousands of markers would blow a
free API budget), this samples the GFS fields we ALREADY ingest — temperature (TMP@2m),
wind (UGRD/VGRD@10m) and humidity (RH@2m) — at the run hour valid "now" (the same hour
the live layers display), bilinearly at each marker's lat/lon. The result is a compact
data/marker_weather.json the markers layer fetches once and reads for its click popups.

Values are therefore the GFS MODEL valid-now, not a station observation: good enough for
a city weather popup, free, instant, and refreshed hourly alongside everything else.

Output schema (data/marker_weather.json):
    {
      "generated": "<iso utc>",          # when this file was written
      "valid_time": "<iso utc>",         # GFS valid time of the sampled hour
      "run": "<YYYYMMDD HHZ>",
      "fhour": <int>,
      "count": <int>,                    # markers with at least one value
      "markers": {
        "<name>|<lat.3f>|<lon.3f>": {"t":<C>, "rh":<%>, "ws":<m/s>, "wd":<deg-from>},
        ...
      }
    }
The frontend builds the same "name|lat|lon" key per feature to join (markers have no id).
"""
import os
import json
import logging
from datetime import datetime, timezone

import numpy as np

from worldmap.lib.config import WorldMapConfig
from worldmap.lib import fieldstore
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class MarkerUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        # Reads the existing "markers" config section (shared with the frontend markers
        # layer); driven by its weather_popup flag and runs_per_day.
        super().__init__(config, "Markers", map_data)
        # Default output if the config section doesn't set one.
        if not self.outfile:
            self.outfile = "data/marker_weather.json"
            self.set_output_path()

    # ---- marker loading -----------------------------------------------------
    def _markers_path(self):
        """Path to the same markers.geojson the frontend serves. Overridable via
        the markers.markers_file setting; defaults to the in-repo UI copy
        (readable by this container via the full-repo mount)."""
        cfg_path = self.settings.get("markers_file")
        if cfg_path:
            return (
                cfg_path
                if os.path.isabs(cfg_path)
                else os.path.join(self.workdir, cfg_path)
            )
        return os.path.join(self.workdir, "ui", "markers", "markers.geojson")

    def _load_markers(self):
        path = self._markers_path()
        with open(path) as f:
            gj = json.load(f)
        markers = []
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            # Only land places get a popup; marine 'feature' entries (seas/straits) don't.
            if props.get("kind") != "place":
                continue
            geom = feat.get("geometry", {}) or {}
            if geom.get("type") != "Point":
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            markers.append(
                {"name": props.get("name", ""), "lat": float(coords[1]), "lon": float(coords[0])}
            )
        return markers

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

    # ---- main ---------------------------------------------------------------
    def _resolve_run_hour(self):
        """Resolve the freshest run + the available hour valid 'now' DIRECTLY from the
        fieldstore catalog, NOT from the cached GFS baseline.

        The GFS baseline that get_gfs_state() establishes is cached in shared_state and
        synced only once per process. In a long-lived layer_builder it therefore drifts
        to an ever-older run as the hours tick by (the forecast hour climbs f00x -> f0NN),
        and once the data collector advances to a newer run and the housekeeper prunes the
        old one, every baseline-based field lookup misses — which is what produced the
        "no fields for f010" skips. Reading the catalog instead samples whatever data is
        genuinely present right now.

        Scoped to temperature+wind (always ingested together for a GFS run) so an unrelated
        model cycle (e.g. RTOFS currents, run id "00") can't outrank the GFS run, and so
        humidity — which can lag a cycle behind, especially right after the RH ingest was
        added — doesn't gate the whole resolution. Returns (run_date, run_id, fhour) or None.
        """
        try:
            store = fieldstore.get_store(self.workdir)
            avail = store.db.get_latest_run_hours(products=["temperature", "wind"])
        except Exception as e:
            logger.warning(f"Markers: catalog lookup failed: {e}")
            return None
        if not avail or not avail.get("hours"):
            return None
        run_date, run_id, hours = avail["run_date"], avail["run_id"], avail["hours"]
        try:
            run_start = datetime.strptime(run_date, "%Y%m%d").replace(
                hour=int(run_id), tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            return None
        # Pick the available forecast hour whose valid time is closest to now.
        target = (datetime.now(timezone.utc) - run_start).total_seconds() / 3600.0
        fhour = min(hours, key=lambda h: abs(int(h) - target))
        return run_date, run_id, int(fhour)

    def run(self):
        if not self.settings.get("weather_popup", False):
            logger.debug("Markers: weather_popup disabled; skipping.")
            return

        # Resolve the run + hour from what's actually in the store (robust to the cached
        # GFS baseline drifting stale in a long-running process).
        resolved = self._resolve_run_hour()
        if not resolved:
            logger.warning(
                "Markers: no temperature/wind fields in the store yet; skipping "
                "(data collector may not have ingested this run)."
            )
            return
        run_date, run_id, fhour = resolved
        # Point the instance state at the resolved run so get_db_field_at_hour reads it.
        self.run_date_str = run_date
        self.run_id = run_id
        self.forecast_hour_str = f"{fhour:03d}"

        try:
            markers = self._load_markers()
        except Exception as e:
            logger.error(f"Markers: could not read markers file: {e}")
            return
        if not markers:
            logger.warning("Markers: no place markers found; nothing to do.")
            return

        lats_q = np.array([m["lat"] for m in markers], dtype=np.float64)
        lons_q = np.array([m["lon"] for m in markers], dtype=np.float64)

        temp_f = self.get_db_field_at_hour("temperature", fhour)
        wind_f = self.get_db_field_at_hour("wind", fhour)
        rh_f = self.get_db_field_at_hour("humidity", fhour)
        # temp+wind are guaranteed present by the resolver; humidity is best-effort.
        if temp_f is None and wind_f is None and rh_f is None:
            logger.warning(
                f"Markers: fields vanished for {run_date} {run_id}Z f{fhour:03d} "
                "between catalog lookup and read; skipping this cycle."
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

        records = {}
        for i, m in enumerate(markers):
            rec = {}
            if t is not None and np.isfinite(t[i]):
                rec["t"] = round(float(t[i]), 1)
            if rh is not None and np.isfinite(rh[i]):
                rec["rh"] = int(round(float(rh[i])))
            if ws is not None and np.isfinite(ws[i]):
                rec["ws"] = round(float(ws[i]), 1)
            if wd is not None and np.isfinite(wd[i]):
                rec["wd"] = int(round(float(wd[i]))) % 360
            if rec:
                records[f"{m['name']}|{m['lat']:.3f}|{m['lon']:.3f}"] = rec

        # Valid time from whichever field provided one.
        valid_iso = None
        for f in (temp_f, wind_f, rh_f):
            if f and f.get("valid_time"):
                vt = f["valid_time"]
                valid_iso = vt.isoformat() if hasattr(vt, "isoformat") else str(vt)
                break

        payload = {
            "generated": datetime.now(timezone.utc).isoformat(),
            "valid_time": valid_iso,
            "run": f"{self.run_date_str} {self.run_id}Z",
            "fhour": fhour,
            "count": len(records),
            "markers": records,
        }
        self._write_json(payload)
        logger.info(
            f"Markers: wrote {len(records)}/{len(markers)} marker records "
            f"for f{fhour:03d} -> {os.path.basename(self.output_path)}"
        )

    def _write_json(self, payload):
        path = self.output_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, path)