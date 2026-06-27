#!/usr/bin/env python3
"""OISST (NOAA high-resolution daily SST) source helpers, shared by the data_collector
(which downloads the yearly netCDF into a file cache) and the sst updater (which renders
from that cache). SST is a single yearly netCDF, not a per-forecast-hour field, so it
lives as a cache file under data/ rather than in the fieldstore.

Keeping the URL + cache-path conventions here means the collector and the renderer can
never disagree on where the file lives or what it's called.
"""
import os
from datetime import datetime, timezone

import requests

# mode -> (remote filename stem, local cache filename)
_OISST = {
    "anomaly": ("sst.day.anom", "noaa_oisst_anomaly.nc"),
    "absolute": ("sst.day.mean", "noaa_oisst_mean.nc"),
}


def oisst_spec(mode):
    """(remote_stem, cache_filename) for a mode; unknown modes fall back to absolute."""
    return _OISST.get((mode or "absolute").strip().lower(), _OISST["absolute"])


def build_oisst_url(base_url, mode, year=None):
    """Yearly OISST netCDF URL for the given mode."""
    year = year or datetime.now().year
    stem, _ = oisst_spec(mode)
    return f"{base_url.rstrip('/')}/{stem}.{year}.nc"


def oisst_cache_path(workdir, mode):
    """Cache path for the netCDF. MUST match Updater.cache_path()'s
    '<section>_cache_<filename>' convention (section 'sst') so the collector writes
    exactly where the sst updater reads."""
    _, fname = oisst_spec(mode)
    return os.path.join(workdir, "data", f"sst_cache_{fname}")


def remote_is_newer(url, dest, timeout=15):
    """True if `dest` is missing, or the remote Last-Modified is newer than the local
    file's mtime (so we only re-download when the source actually changed). On any HEAD
    error we conservatively report False (keep the existing cache) unless dest is absent.
    """
    if not os.path.exists(dest):
        return True
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return False
        lm = resp.headers.get("Last-Modified")
        if not lm:
            return False
        remote_mtime = datetime.strptime(lm, "%a, %d %b %Y %H:%M:%S %Z").replace(
            tzinfo=timezone.utc
        )
        local_mtime = datetime.fromtimestamp(os.path.getmtime(dest), tz=timezone.utc)
        return remote_mtime > local_mtime
    except Exception:
        return False
