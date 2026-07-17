#!/usr/bin/env python3
"""NASA FIRMS VIIRS_NOAA20_NRT active-fire feed -> database.

Pure data (no render): fetches the FIRMS area CSV (world bbox, most recent day) and
upserts rows into the DB. The frontend reads them via the /api/fires route.

Source is VIIRS_NOAA20_NRT, not VIIRS_SNPP_NRT -- Suomi-NPP's NRT feed was confirmed
returning zero detections (empty CSV body) against world bbox, multiple day_range
values, and a known-hot region, while NOAA-20/21 and MODIS all returned normal global
data against the same key at the same time. NOAA-20 is the newer, currently-operational
VIIRS satellite; if it degrades the same way, VIIRS_NOAA21_NRT is the other confirmed-
working VIIRS source (see _CONFIDENCE_MAP below -- both NOAA-20/21 share the same
single-letter confidence encoding, unlike SNPP's full words).

Requires a free FIRMS MAP_KEY (https://firms.modaps.eosdis.nasa.gov/api/map_key/),
injected the same way AIS_API_KEY/OPENWEATHER_API_KEY are (see lib/config.py's
_inject_secrets) -- never stored in config.json itself.

No has_new_data() override: FIRMS' area endpoint is a dynamically generated response,
not a static file, so it carries no reliable ETag/Last-Modified to HEAD-check against.
Every stale-scheduled cycle (is_stale(), from runs_per_day) just re-fetches.

VIIRS' global detection volume (thousands/day) is orders of magnitude higher than
quakes/volcanoes, so unlike those, this collector prunes expired rows itself after
every successful fetch (FireAdapter.delete_expired) rather than letting the table grow
unbounded forever.
"""
import io
import logging

import requests
import pandas as pd

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.fire_adapter import FireAdapter

logger = logging.getLogger(__name__)

_WORLD_BBOX = "-180,-90,180,90"
_SOURCE = "VIIRS_NOAA20_NRT"
_DAY_RANGE = 1

# NOAA-20/21's confidence field is single-letter (l/n/h), unlike SNPP's full words --
# normalized here at ingest so FireAdapter/the frontend/the config UI's SelectSpec only
# ever need to know one vocabulary ("low"/"nominal"/"high"). Already-full-word values
# pass through unchanged (defensive, in case a future source uses SNPP's convention).
_CONFIDENCE_MAP = {"l": "low", "n": "nominal", "h": "high"}


def _normalize_confidence(raw) -> str:
    key = str(raw).strip().lower()
    return _CONFIDENCE_MAP.get(key, key or "nominal")


class FiresCollector(CollectorBase):
    section = "fires"
    channel_key = "fires"
    datasource_key = "fires"

    def __init__(self, config):
        super().__init__(config)
        self.fire_adapter = FireAdapter()

    def collect(self) -> None:
        """Fetch the FIRMS VIIRS area CSV and upsert into the database, then prune
        rows past expiry_hours."""
        base_url = self.datasource_url("fires")
        api_key = (self.settings.get("api_key") or "").strip()
        expiry_hours = float(self.settings.get("expiry_hours", 24))

        if not base_url:
            logger.warning("Fires: no URL configured; skipping.")
            return
        if not api_key:
            logger.warning("Fires: no FIRMS API key configured; skipping.")
            return

        url = f"{base_url}/{api_key}/{_SOURCE}/{_WORLD_BBOX}/{_DAY_RANGE}"
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "AtmosGL-Collector/1.0"})
            r.raise_for_status()

            df = pd.read_csv(io.StringIO(r.text))
            if "latitude" not in df.columns:
                logger.error(f"Fires: unexpected response (not a fire CSV): {r.text[:200]!r}")
                return

            rows = []
            for _, row in df.iterrows():
                acq_date = str(row["acq_date"])
                acq_time = str(int(row["acq_time"])).zfill(4)
                acq_time_iso = f"{acq_date}T{acq_time[:2]}:{acq_time[2:]}:00+00:00"
                lat, lon = float(row["latitude"]), float(row["longitude"])
                satellite = str(row.get("satellite", ""))
                fire_id = f"{satellite}|{lat:.4f}|{lon:.4f}|{acq_date}|{acq_time}"

                rows.append(
                    {
                        "id": fire_id,
                        "lat": lat,
                        "lon": lon,
                        "brightness": float(row.get("bright_ti4", row.get("brightness", 0.0)) or 0.0),
                        "frp": float(row.get("frp", 0.0) or 0.0),
                        "confidence": _normalize_confidence(row.get("confidence", "low")),
                        "satellite": satellite,
                        "daynight": str(row.get("daynight", "")),
                        "acq_time": acq_time_iso,
                    }
                )

            self.fire_adapter.upsert_fires(rows)
            deleted = self.fire_adapter.delete_expired(expiry_hours)
            logger.info(f"Fires: upserted {len(rows)} detections, pruned {deleted} expired.")
        except requests.RequestException as e:
            logger.error(f"Fires: fetch failed: {e}")
