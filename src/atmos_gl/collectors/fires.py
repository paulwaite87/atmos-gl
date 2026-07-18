#!/usr/bin/env python3
"""NASA FIRMS VIIRS active-fire feed (NOAA-20 + NOAA-21) -> database.

Pure data (no render): fetches the FIRMS area CSV (world bbox, most recent day) for
each source in _SOURCES and upserts the combined rows into the DB. The frontend reads
them via the /api/fires route.

Two sources, not one -- VIIRS_NOAA20_NRT alone misses real detections that NOAA-21's
independent overpass catches (and vice versa): same sensor design, different orbit
timing, so together they roughly double the revisit frequency over any given point.
Not VIIRS_SNPP_NRT -- Suomi-NPP's NRT feed was confirmed returning zero detections
(empty CSV body) against world bbox, multiple day_range values, and a known-hot region,
while NOAA-20/21 and MODIS all returned normal global data against the same key at the
same time. NOAA-20/21 share the same single-letter confidence encoding (see
_CONFIDENCE_MAP below), unlike SNPP's full words.

Amalgamating the two sources needs no dedup step: each row's `id` embeds `satellite`
("N20"/"N21", read straight from FIRMS' own `satellite` column) alongside lat/lon/time,
so NOAA-20 and NOAA-21 detections always upsert as distinct rows in the same `fires`
table (FireAdapter.upsert_fires) -- fetching both and upserting together is sufficient;
the frontend/route layer is already source-agnostic (keys off confidence/frp/age, never
`satellite`).

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
_SOURCES = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
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
        """Fetch every source in _SOURCES independently and upsert the combined
        detections into the database, then prune rows past expiry_hours.

        Every source is ATTEMPTED regardless of whether an earlier one failed (so one
        satellite's outage doesn't block the other's detections from landing), but if
        any source failed this raises afterward -- mirrors SstCollector.collect()'s
        per-mode independence, for the same reason: _drive() (collectors/__init__.py)
        only records success=True/advances last_updated when collect() returns without
        raising, so swallowing a per-source failure here would let the Data Status UI
        report 100% while that satellite's detections silently went stale.
        """
        base_url = self.datasource_url("fires")
        api_key = (self.settings.get("api_key") or "").strip()
        expiry_hours = float(self.settings.get("expiry_hours", 24))

        if not base_url:
            logger.warning("Fires: no URL configured; skipping.")
            return
        if not api_key:
            logger.warning("Fires: no FIRMS API key configured; skipping.")
            return

        rows = []
        errors = []
        for source in _SOURCES:
            try:
                rows.extend(self._fetch_source(base_url, api_key, source))
            except requests.RequestException as e:
                logger.error(f"Fires: {source} fetch failed: {e}")
                errors.append(f"{source}: {e}")

        self.fire_adapter.upsert_fires(rows)
        deleted = self.fire_adapter.delete_expired(expiry_hours)
        logger.info(
            f"Fires: upserted {len(rows)} detections across {len(_SOURCES)} source(s), "
            f"pruned {deleted} expired."
        )

        if errors:
            raise RuntimeError(
                f"Fires: failed to fetch {len(errors)}/{len(_SOURCES)} source(s): "
                + "; ".join(errors)
            )

    def _fetch_source(self, base_url: str, api_key: str, source: str) -> list[dict]:
        """Fetch and parse one VIIRS source's FIRMS area CSV into row dicts ready for
        FireAdapter.upsert_fires(). Raises requests.RequestException on network/HTTP
        failure (caller decides whether that's fatal for the overall collect); returns
        [] (logged) for an unexpected non-CSV response body, same as the single-source
        collector did before this source was one of several."""
        url = f"{base_url}/{api_key}/{source}/{_WORLD_BBOX}/{_DAY_RANGE}"
        r = requests.get(url, timeout=30, headers={"User-Agent": "AtmosGL-Collector/1.0"})
        r.raise_for_status()

        df = pd.read_csv(io.StringIO(r.text))
        if "latitude" not in df.columns:
            logger.error(f"Fires: {source} unexpected response (not a fire CSV): {r.text[:200]!r}")
            return []

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
        return rows
