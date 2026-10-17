#!/usr/bin/env python3
"""Sea Ice layer rendering (Updater): SeaIceCollector's cached OISST ice-concentration
netCDF (lib/sea_ice.py) encoded as a raw data texture for ui/modules/sea_ice.js, issue
#312's client-side-palette convention -- the colour ramp, the 15% cutoff and opacity are
applied in the browser, so none of them re-renders anything here.

Only the newest day in the file is drawn. Concentration is encoded in percent over a
FIXED 0-100 domain, so the pack always reads against the same scale. The grid is used at
its native 0.25 degrees: flipped north first and re-ordered onto the project's -180..179
longitude axis, the layout the GPU fill texture needs. NaN (no ice: open water and land
alike) encodes as transparent. A sidecar carries the day shown for the legend.
"""
import logging
import os

import numpy as np
import xarray as xr

from terrascope.lib.config import TerrascopeConfig
from terrascope.lib.sea_ice import sea_ice_cache_path
from terrascope.lib.texture import encode_frames
from .common import MapData, Updater

logger = logging.getLogger(__name__)

# Concentration, percent. Mirrored by ui/modules/sea_ice.js's ENCODE_DOMAIN.
ENCODE_DOMAIN = (0.0, 100.0)


def texture_field(fraction: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """OISST's (lat south first, lon 0..360) concentration fraction as a (north-first,
    lon -180..179) field in percent."""
    order = np.argsort(((lon + 180.0) % 360.0) - 180.0)
    return (np.asarray(fraction, dtype=np.float32)[::-1][:, order] * 100.0).astype(np.float32)


class SeaIceUpdater(Updater):
    def __init__(self, config: TerrascopeConfig, map_data: MapData):
        super().__init__(config, "sea_ice", map_data)

    def run(self, max_hours=None):
        # max_hours is a no-op: one latest day, nothing per forecast hour.
        nc_path = sea_ice_cache_path(self.workdir)
        if not os.path.exists(nc_path):
            return
        # Nothing render-relevant is configurable (the palette is client-side), so the
        # data's mtime is the whole freshness check.
        sig = self._settings_signature({})
        if self._is_render_fresh(self.output_path, [nc_path], sig):
            return
        with xr.open_dataset(nc_path) as ds:
            latest = ds.isel(time=-1)
            fraction = latest["icec"].values.squeeze()
            lon = latest["lon"].values
            day = str(np.datetime_as_string(latest["time"].values, unit="D"))
        encode_frames([texture_field(fraction, lon)], self.output_path, *ENCODE_DOMAIN)
        self._write_meta_sidecar("sea_ice_meta.json", "sea_ice", {"date": day})
        self._write_render_signature(self.output_path, sig)
        logger.info(f"{self.section}: rendered sea ice texture for {day}.")
