#!/usr/bin/env python3
"""Population Density layer data: the European Commission JRC's Global Human
Settlement Layer population grid (GHS-POP R2023A, 2025 epoch, 30 arc-second WGS84),
summed onto a 0.05deg global grid and stored as people per km².

GHS-POP is published as one ~484MB zip holding a single global GeoTIFF of resident
people per ~1km cell -- open data (CC BY 4.0), no account or token. It is a modelled
epoch, not a live feed: the release in use (R2023A) covers 1975-2030 in five-year
steps, and the JRC publishes a new release every couple of years under a NEW URL.
The URL therefore lives in data_collector.datasources.population_density, so moving
to a newer release or epoch is a config change; PopulationDensityCollector also
re-fetches if the JRC republishes the same file (its Last-Modified changes).

Download: confirmed live that a single stream from the JRC's server runs well under
100KB/s (hours for the whole zip), while each HTTP Range request gets about 500KB/s
in parallel. download_chunks() therefore fetches fixed-size ranges on a few
threads, each into its own file, so a download interrupted by a cycle's time budget
or a restart resumes rather than starting over.
"""
import hashlib
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

logger = logging.getLogger(__name__)

GHSL_POP_URL = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
    "GHS_POP_E2025_GLOBE_R2023A_4326_30ss/V1-0/GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.zip"
)
# The dataset's landing page -- the Data Status link, rather than the 484MB zip itself.
GHSL_POP_PAGE_URL = "https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php"

GRID_STEP_DEG = 0.05
CHUNK_BYTES = 16 * 1024 * 1024
DOWNLOAD_WORKERS = 8

# Mean radius of the WGS84 authalic sphere, km -- cell areas below.
_EARTH_RADIUS_KM = 6371.0072
# Source rows read per strip during aggregation: 240 rows x 43200 columns of float32
# is ~41MB, keeping the read well clear of the data_collector's memory.
_STRIP_ROWS = 240


def _download_root() -> str:
    """Container-local working dir for the chunked zip download -- NOT bind-mounted,
    same convention as lib/flood_risk.py's per-tile cache: only the finished density
    grid needs to survive a container rebuild."""
    return os.path.join(os.path.expanduser("~"), ".local", "share", "population_density")


def density_grid_cache_path(workdir: str) -> str:
    """Under {workdir}/data (bind-mounted): written by PopulationDensityCollector in
    data_collector, read by PopulationDensityUpdater in layer_builder."""
    return os.path.join(workdir, "data", "population_density_cache_grid.npz")


def source_marker_cache_path(workdir: str) -> str:
    return os.path.join(workdir, "data", "population_density_cache_source.json")


def remote_file_info(url: str, timeout: int = 15) -> dict:
    """{"url", "size", "marker"} from a HEAD request -- marker is the ETag (or
    Last-Modified), identifying this exact copy of the file. Raises on failure."""
    import requests

    r = requests.head(url, timeout=timeout, allow_redirects=True,
                      headers={"User-Agent": "AtmosGL-Collector/1.0"})
    r.raise_for_status()
    return {
        "url": url,
        "size": int(r.headers["Content-Length"]),
        "marker": r.headers.get("ETag") or r.headers.get("Last-Modified") or "",
    }


def cached_source(workdir: str) -> dict | None:
    """The {"url", "marker"} the cached density grid was built from, or None."""
    try:
        with open(source_marker_cache_path(workdir)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_cached_source(workdir: str, remote: dict) -> None:
    path = source_marker_cache_path(workdir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump({"url": remote["url"], "marker": remote["marker"]}, f)
    os.replace(tmp_path, path)


def is_current(workdir: str, remote: dict) -> bool:
    cached = cached_source(workdir)
    return (
        cached is not None
        and cached.get("url") == remote["url"]
        and cached.get("marker") == remote["marker"]
        and os.path.exists(density_grid_cache_path(workdir))
    )


def part_dir_for(remote: dict) -> str:
    """One download dir per (url, marker): a republished or different file never
    mixes its chunks with an older one's."""
    key = hashlib.sha1(f"{remote['url']}|{remote['marker']}".encode()).hexdigest()[:12]
    return os.path.join(_download_root(), key)


def _chunk_ranges(size: int) -> list[tuple[int, int]]:
    return [(start, min(start + CHUNK_BYTES, size) - 1) for start in range(0, size, CHUNK_BYTES)]


def _chunk_path(part_dir: str, index: int) -> str:
    return os.path.join(part_dir, f"chunk_{index:04d}")


def chunk_progress(remote: dict) -> tuple[int, int]:
    """(chunks downloaded, total chunks) for remote's download."""
    part_dir = part_dir_for(remote)
    ranges = _chunk_ranges(remote["size"])
    done = sum(1 for i in range(len(ranges)) if os.path.exists(_chunk_path(part_dir, i)))
    return done, len(ranges)


def _fetch_chunk(url: str, part_dir: str, index: int, start: int, end: int, timeout: int) -> None:
    import requests

    r = requests.get(url, timeout=timeout, headers={
        "User-Agent": "AtmosGL-Collector/1.0", "Range": f"bytes={start}-{end}",
    })
    r.raise_for_status()
    if r.status_code != 206 or len(r.content) != end - start + 1:
        raise IOError(f"chunk {index}: expected {end - start + 1} bytes of a 206, "
                      f"got {len(r.content)} bytes of a {r.status_code}")
    path = _chunk_path(part_dir, index)
    with open(f"{path}.tmp", "wb") as f:
        f.write(r.content)
    os.replace(f"{path}.tmp", path)


def download_chunks(remote: dict, budget_s: float, workers: int = DOWNLOAD_WORKERS,
                    timeout: int = 120, on_progress=None) -> bool:
    """Fetch remote's missing chunks until all are on disk or budget_s runs out (no
    new chunk starts after that; ones in flight finish). True once every chunk is
    present. A failed chunk is logged and retried next call. on_progress(done, total)
    is called after each chunk lands."""
    part_dir = part_dir_for(remote)
    os.makedirs(part_dir, exist_ok=True)
    ranges = _chunk_ranges(remote["size"])
    missing = [i for i in range(len(ranges)) if not os.path.exists(_chunk_path(part_dir, i))]
    deadline = time.monotonic() + budget_s

    def fetch(index):
        if time.monotonic() >= deadline:
            return
        start, end = ranges[index]
        try:
            _fetch_chunk(remote["url"], part_dir, index, start, end, timeout)
        except Exception as e:
            logger.warning(f"population_density: chunk {index} failed ({e}); will retry.")
            return
        if on_progress:
            on_progress(*chunk_progress(remote))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fetch, missing))
    done, total = chunk_progress(remote)
    return done == total


def assemble_zip(remote: dict) -> str:
    """Concatenate remote's chunks into one zip beside them; returns its path."""
    part_dir = part_dir_for(remote)
    dest = os.path.join(part_dir, "source.zip")
    with open(f"{dest}.tmp", "wb") as out:
        for i in range(len(_chunk_ranges(remote["size"]))):
            with open(_chunk_path(part_dir, i), "rb") as f:
                shutil.copyfileobj(f, out)
    os.replace(f"{dest}.tmp", dest)
    return dest


def discard_download(remote: dict) -> None:
    shutil.rmtree(part_dir_for(remote), ignore_errors=True)


def build_grid(step_deg: float = GRID_STEP_DEG):
    """Full-globe cell-centre axes, north first (row 0 = the north pole's band) --
    the layout encode_frames' GPU texture needs."""
    n_lat = round(180.0 / step_deg)
    n_lon = round(360.0 / step_deg)
    lat = 90.0 - step_deg / 2.0 - np.arange(n_lat) * step_deg
    lon = -180.0 + step_deg / 2.0 + np.arange(n_lon) * step_deg
    return lat, lon


def cell_areas_km2(lat, step_deg: float = GRID_STEP_DEG) -> np.ndarray:
    """Area of one grid cell in each latitude row, km² (spherical)."""
    north = np.radians(np.minimum(90.0, lat + step_deg / 2.0))
    south = np.radians(np.maximum(-90.0, lat - step_deg / 2.0))
    return _EARTH_RADIUS_KM ** 2 * np.radians(step_deg) * (np.sin(north) - np.sin(south))


def _geotiff_in_zip(zip_path: str) -> str:
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".tif")]
    if not names:
        raise ValueError(f"no GeoTIFF in {zip_path}")
    return f"/vsizip/{zip_path}/{names[0]}"


def aggregate_people(raster_path: str, step_deg: float = GRID_STEP_DEG):
    """(people, lat, lon): the source raster's people-per-pixel summed into each
    build_grid() cell. Reads in row strips, assigning each source pixel to the grid
    cell holding its centre, so the source needn't align to the grid. Nodata and
    negative values count as nobody. raster_path may be a GDAL /vsizip/ path."""
    import rasterio
    from rasterio.windows import Window

    lat, lon = build_grid(step_deg)
    people = np.zeros((len(lat), len(lon)), dtype=np.float64)

    with rasterio.open(raster_path) as src:
        t = src.transform
        src_lon = t.c + (np.arange(src.width) + 0.5) * t.a
        col_idx = np.clip(((src_lon + 180.0) / step_deg).astype(np.int64), 0, len(lon) - 1)
        col_starts = np.flatnonzero(np.r_[True, np.diff(col_idx) != 0])
        col_targets = col_idx[col_starts]

        for row0 in range(0, src.height, _STRIP_ROWS):
            rows = min(_STRIP_ROWS, src.height - row0)
            strip = src.read(1, window=Window(0, row0, src.width, rows)).astype(np.float64)
            strip[~(strip > 0)] = 0.0  # nodata (GHSL: -200), NaN, negatives
            src_lat = t.f + (row0 + np.arange(rows) + 0.5) * t.e
            row_idx = np.clip(((90.0 - src_lat) / step_deg).astype(np.int64), 0, len(lat) - 1)
            row_starts = np.flatnonzero(np.r_[True, np.diff(row_idx) != 0])
            block = np.add.reduceat(np.add.reduceat(strip, col_starts, axis=1), row_starts, axis=0)
            people[np.ix_(row_idx[row_starts], col_targets)] += block

    return people, lat, lon


def density_from_zip(zip_path: str, step_deg: float = GRID_STEP_DEG):
    """(density, lat, lon): people per km² on build_grid(), float32."""
    people, lat, lon = aggregate_people(_geotiff_in_zip(zip_path), step_deg)
    density = people / cell_areas_km2(lat, step_deg)[:, None]
    return density.astype(np.float32), lat, lon


def save_density_grid(path: str, density, lat, lon) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as f:
        np.savez_compressed(f, density=density.astype(np.float32), lat=lat, lon=lon)
    os.replace(tmp_path, path)


def load_density_grid(path: str):
    """(density, lat, lon) from save_density_grid()'s file."""
    with np.load(path) as z:
        return z["density"], z["lat"], z["lon"]
