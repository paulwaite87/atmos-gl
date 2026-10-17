#!/usr/bin/env python3
"""Sea Ice layer data: NOAA's OISST daily sea-ice concentration (the `icec` file that sits
beside the SST ones on the PSL download server), a 0.25 degree global grid -- fraction of
each cell covered by ice, 0..1, NaN where there is no ice (open water and land alike) --
with one field per day, a yearly file that grows by a day as NOAA publishes it, about
two days behind the present. Open US-government data, no key.

The yearly netCDF is cached as downloaded; tasks/sea_ice.py renders the latest day from
it. The URL convention and the cache path live here so the collector and the updater can
never disagree about where the file is.
"""
import os

ICE_STEM = "icec.day.mean"
OISST_BASE_URL = "https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres"
# The dataset's landing page -- the Data Status link, rather than the raw netCDF.
OISST_PAGE_URL = "https://psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html"


def sea_ice_url(base_url: str, year: int) -> str:
    """The yearly ice-concentration netCDF."""
    return f"{base_url.rstrip('/')}/{ICE_STEM}.{year}.nc"


def sea_ice_cache_path(workdir: str) -> str:
    """Under {workdir}/data (bind-mounted): written by SeaIceCollector in data_collector,
    read by SeaIceUpdater in layer_builder. One file, whichever year it was fetched from."""
    return os.path.join(workdir, "data", "sea_ice_cache_icec.nc")
