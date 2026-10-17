#!/usr/bin/env python3
"""Aurora layer data: NOAA SWPC's OVATION aurora forecast -- the probability (0-100) of
seeing aurora on a global one-degree grid, 360 longitudes (0..359) by 181 latitudes
(-90..90), published as one JSON document of [lon, lat, probability] triples together
with an observation time and a forecast time about an hour apart. Open US-government
data, no key. The feed is refreshed about every five minutes.

The grid is cached as it arrives -- (lat, lon) = (181, 360), south first, longitude
0..359 -- with the two times beside it; tasks/aurora.py turns it into the globe texture.
"""
import os
from datetime import datetime

import numpy as np

OVATION_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
# The dataset's landing page -- the Data Status link, rather than the raw JSON.
OVATION_PAGE_URL = "https://www.swpc.noaa.gov/products/aurora-30-minute-forecast"

N_LAT, N_LON = 181, 360


def aurora_grid_cache_path(workdir: str) -> str:
    """Under {workdir}/data (bind-mounted): written by AuroraCollector in data_collector,
    read by AuroraUpdater in layer_builder."""
    return os.path.join(workdir, "data", "aurora_cache_grid.npz")


def _check_time(value) -> str:
    """The feed's times are ISO-8601 UTC ("2026-10-17T05:53:00Z"); anything else means the
    document is not the one we know."""
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def parse_ovation(doc: dict):
    """(probability[lat, lon] uint8, observation_time, forecast_time) from the feed.
    Raises ValueError (or KeyError/TypeError) for anything but a complete, in-range
    document, so a truncated or changed feed is rejected rather than half-stored."""
    observed = _check_time(doc["Observation Time"])
    forecast = _check_time(doc["Forecast Time"])
    triples = np.asarray(doc["coordinates"], dtype=np.int64)
    if triples.shape != (N_LAT * N_LON, 3):
        raise ValueError(f"expected {N_LAT * N_LON} [lon, lat, value] triples, got {triples.shape}")
    lon, lat, value = triples.T
    if lon.min() < 0 or lon.max() >= N_LON or lat.min() < -90 or lat.max() > 90:
        raise ValueError("grid coordinates outside lon 0..359 / lat -90..90")
    if value.min() < 0 or value.max() > 100:
        raise ValueError("probability outside 0..100")
    probability = np.zeros((N_LAT, N_LON), dtype=np.uint8)
    probability[lat + 90, lon] = value
    return probability, observed, forecast


def save_aurora_grid(path: str, probability, observed: str, forecast: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as f:
        np.savez_compressed(
            f, probability=probability, observed=np.array(observed), forecast=np.array(forecast)
        )
    os.replace(tmp_path, path)


def load_aurora_grid(path: str):
    """(probability, observation_time, forecast_time) from save_aurora_grid()'s file."""
    with np.load(path) as z:
        return z["probability"], str(z["observed"]), str(z["forecast"])
