"""Standalone RTOFS (ocean) data helpers, the ocean-model twin of gfs.py.

RTOFS Global runs ONCE per day (cycle 00Z), published to NOMADS at roughly 16:00 UTC.
Unlike GFS (6-hourly runs, GRIB2 with .idx byte-range access), RTOFS surface files are
whole NetCDF files we download in full. The forecast-hour file naming mirrors GFS:

    rtofs.{YYYYMMDD}/rtofs_glo_2ds_f{NNN}_prog.nc   (forecast hour NNN from analysis)
    rtofs.{YYYYMMDD}/rtofs_glo_2ds_n000_prog.nc     (nowcast at analysis time = 'now')

f000 and n000 are the same instant (the run's analysis time); fNNN looks forward.
Surface forecast files are hourly for f000..f072, then 3-hourly to f192 — the collector
caps currents at <=72h so it can use a simple hourly loop and never request a
non-existent hour.
"""

import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

NOMADS_RTOFS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod"

# Hourly forecast files only exist out to this hour; beyond it RTOFS is 3-hourly.
RTOFS_MAX_HOURLY_FHOUR = 72


def build_currents_url(base_url, date_str, fhour):
    """Forecast-hour surface prog file URL (the main pattern)."""
    return f"{base_url}/rtofs.{date_str}/rtofs_glo_2ds_f{int(fhour):03d}_prog.nc"


def build_currents_nowcast_url(base_url, date_str):
    """Nowcast-at-analysis-time URL (n000) — the 'present conditions' fallback."""
    return f"{base_url}/rtofs.{date_str}/rtofs_glo_2ds_n000_prog.nc"


def resolve_rtofs_baseline(base_url=NOMADS_RTOFS_BASE, search_days=2):
    """Find the newest available RTOFS run by probing its f000 surface prog file.

    RTOFS is a single daily 00Z cycle; we treat the analysis time as 00Z of the run
    date. Returns {date_str, date_str_Y_M_D, run, timestamp(utc)} or None. The run is
    always "00" (one cycle/day) — kept in the dict for symmetry with the GFS baseline
    so the collector/fieldstore key scheme (date, run, fhour, product) is unchanged.
    """
    now = datetime.now(timezone.utc)
    for day_offset in range(search_days):
        target_date = now - timedelta(days=day_offset)
        date_str = target_date.strftime("%Y%m%d")
        url = build_currents_url(base_url, date_str, 0)
        try:
            if requests.head(url, timeout=8).status_code == 200:
                ts = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                logger.debug(f"RTOFS baseline: {date_str} 00Z")
                return {
                    "date_str": date_str,
                    "date_str_Y_M_D": target_date.strftime("%Y-%m-%d"),
                    "run": "00",
                    "timestamp": ts,
                }
        except requests.RequestException:
            continue
    return None
