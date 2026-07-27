#!/usr/bin/env python3
"""Shared helpers for the greenhouse-gas (CO2/CH4) layer, used by both the
collectors (collectors/greenhouse_gases.py) and the updater (tasks/greenhouse_gases.py)
-- same "one source of truth for path/URL conventions" role lib/oisst.py plays for SST.

Both Absolute (current conditions) and Anomaly (vs. a historical baseline year) are
sourced from Copernicus CAMS via the CDS API, covering CO2 and CH4 together --
NASA GEOS-CF was the original Absolute-mode choice, but live testing against its real
OPeNDAP server found it does not serve CO2 at all (only CH4), so both modes now share
one data source family. See the published spec's "Further Notes"/issue comments for
the correction.

Unlike SST, no upstream source ships a pre-computed CO2/CH4 anomaly: it's the current
CAMS forecast field minus a CAMS EGG4 historical baseline field, computed here. The two
sources are on different native grids (forecast ~0.1 deg, EGG4 reanalysis ~0.75 deg), so
the baseline is upsampled onto the current field's finer grid before subtracting --
downsampling the current field to match the baseline's coarser grid instead would make
Anomaly mode visibly blockier than Absolute mode when a user switches between them.
"""
import os

import numpy as np
from scipy.interpolate import RegularGridInterpolator

SPECIES = ("co2", "ch4")

# Years CAMS EGG4 (the anomaly baseline source) actually covers -- the reanalysis was
# never extended past 2020, so a baseline_year outside this range has no gridded data
# to fetch. See the GHG design grill for why this range is a hard restriction rather
# than gap-filled with an approximation.
BASELINE_YEAR_MIN = 2003
BASELINE_YEAR_MAX = 2020


def resolve_baseline_year(settings: dict) -> int:
    """The configured baseline_year, clamped into CAMS EGG4's actual coverage
    (BASELINE_YEAR_MIN..MAX) and falling back to BASELINE_YEAR_MIN on anything
    unparseable. Shared by CamsEgg4BaselineCollector and GhgUpdater so the two never
    drift on what "the configured year" means."""
    try:
        year = int(settings.get("baseline_year", BASELINE_YEAR_MIN))
    except (TypeError, ValueError):
        year = BASELINE_YEAR_MIN
    return min(max(year, BASELINE_YEAR_MIN), BASELINE_YEAR_MAX)


def camsforecast_cache_path(workdir: str) -> str:
    """Cache path for the current CO2+CH4 netCDF (CAMS global greenhouse gas
    forecasts, leadtime_hour=0 -- the nearest-to-now analysis-initialised step)."""
    return os.path.join(workdir, "data", "greenhouse_gases_cache_camsforecast.nc")


def egg4_baseline_cache_path(workdir: str, baseline_year: int) -> str:
    """Cache path for the CAMS EGG4 baseline netCDF for a given year. One file per
    year currently cached -- CamsEgg4BaselineCollector only ever keeps the file for
    the CURRENTLY configured baseline_year (see its collect()), so this doesn't
    accumulate one file per year ever requested."""
    return os.path.join(
        workdir, "data", f"greenhouse_gases_cache_egg4_{int(baseline_year)}.nc"
    )


def regrid_onto(field, lats, lons, target_lats, target_lons, fill_value=np.nan):
    """Resample `field` (lat x lon 2D array) from its own (lats, lons) axes onto
    (target_lats, target_lons) via bilinear interpolation. Pure function -- no I/O,
    no Updater instance required (unlike Updater.regrid_for_lod, which resamples onto
    a level-of-detail TIER's step size rather than another field's exact grid)."""
    lats = np.asarray(lats, dtype=np.float64)
    field = np.asarray(field, dtype=np.float64)
    if lats[0] > lats[-1]:
        lats_inc, field_inc = lats[::-1], field[::-1, :]
    else:
        lats_inc, field_inc = lats, field

    fn = RegularGridInterpolator(
        (lats_inc, lons), field_inc, bounds_error=False, fill_value=fill_value
    )
    mesh_lats, mesh_lons = np.meshgrid(target_lats, target_lons, indexing="ij")
    return fn((mesh_lats, mesh_lons))


def compute_anomaly(
    current_field, current_lats, current_lons, baseline_field, baseline_lats, baseline_lons
):
    """current_field minus baseline_field, with baseline_field upsampled onto
    current_field's (finer) grid first. Returns an array shaped like current_field."""
    baseline_on_current_grid = regrid_onto(
        baseline_field, baseline_lats, baseline_lons, current_lats, current_lons
    )
    return np.asarray(current_field, dtype=np.float64) - baseline_on_current_grid
