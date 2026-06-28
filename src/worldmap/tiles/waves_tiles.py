#!/usr/bin/env python3
"""Web-Mercator raster tiles for the waves layer.

Tiles render directly in Web Mercator (no matplotlib/cartopy) so there's no
reprojection step and no resolution ceiling. Per tile we: inverse-Mercator each
pixel, bilinear-sample a baked global wave field, palette-LUT the height, and cut
land with real coastline geometry (vector, crisp at any zoom).

Model is "publish then fill":

  * On a data/settings change the builder bakes the global field and PUBLISHES the new
    version immediately (``published.json``), before rendering any display tiles.
  * The API renders ANY missing tile on demand and caches it — so the moment the
    frontend switches version, MapLibre requests only the *visible* tiles and those
    render first. The viewport is prioritised for free.
  * The builder then warms the base pyramid (z0..prebuild_maxzoom, low zoom first) in
    the background, skipping tiles on-demand already produced.

Land masking uses an STRtree of coastline polygons: open-ocean tiles match nothing in
the index and skip the containment test entirely; coastal tiles test only the few
local polygons that overlap them. That's what keeps both the warm and the on-demand
renders fast.
"""

import os
import io
import json
import shutil
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

logger = logging.getLogger("worldmap.tiles.waves")

LAT_LIMIT = 85.0511
TILE_PX = 256
VMAX = 8.0
PREBUILD_MAXZOOM_DEFAULT = 6  # base pyramid depth warmed on refresh
ONDEMAND_MAXZOOM = 9  # real tiles served to here (deeper still renders)

_lut_cache: dict[str, np.ndarray] = {}
_field_mem: dict[str, tuple] = {}  # version -> (field, meta), API-side
_coast_tree = None
_coast_polys = None
_coast_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Tile geometry / palette / sampling (pure, unit-tested)
# --------------------------------------------------------------------------- #
def tile_pixel_lonlat(z: int, x: int, y: int, px: int = TILE_PX):
    n = 2.0**z
    col = (np.arange(px) + 0.5) / px
    lon = (x + col) / n * 360.0 - 180.0
    row = (np.arange(px) + 0.5) / px
    merc = np.pi * (1.0 - 2.0 * (y + row) / n)
    lat = np.degrees(np.arctan(np.sinh(merc)))
    return lon, lat


def build_lut(palette_name: str) -> np.ndarray:
    if palette_name in _lut_cache:
        return _lut_cache[palette_name]
    import matplotlib.colors as mcolors
    from worldmap.tasks.waves import PALETTES

    rgb = PALETTES.get(palette_name) or next(iter(PALETTES.values()))
    cmap = mcolors.LinearSegmentedColormap.from_list("wave_height", rgb, N=256)
    lut = (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255.0).astype(np.uint8)
    _lut_cache[palette_name] = lut
    return lut


def sample_field(field, meta, lon2d, lat2d):
    from scipy.ndimage import map_coordinates

    col = ((lon2d - meta["lon0"]) / meta["dlon"]) % meta["nlon"]
    row = np.clip((lat2d - meta["lat0"]) / meta["dlat"], 0, meta["nlat"] - 1)
    return map_coordinates(
        field, [row, col], order=1, mode="grid-wrap", prefilter=False
    )


def compose_tile_rgba(
    field, meta, lut, alpha255, threshold, z, x, y, land_fn=None, px=TILE_PX
):
    lon, lat = tile_pixel_lonlat(z, x, y, px)
    lon2d, lat2d = np.meshgrid(lon, lat)
    swh = sample_field(field, meta, lon2d, lat2d)

    idx = (np.clip(swh / VMAX, 0.0, 1.0) * 255.0).astype(np.uint8)
    rgb = lut[idx]

    alpha = np.full(swh.shape, alpha255, dtype=np.uint8)
    if threshold > 0.0:
        alpha[swh < threshold] = 0
    land = (land_fn or land_mask)(lon2d, lat2d)
    if land is not None:
        alpha[land] = 0
    return np.dstack([rgb, alpha[..., None]]).astype(np.uint8)


# --------------------------------------------------------------------------- #
# Coastline: STRtree of polygons, queried per-tile (local mask, fast)
# --------------------------------------------------------------------------- #
def _coastline():
    """Build (once) and return an STRtree of coastline polygons + the polygon list."""
    global _coast_tree, _coast_polys
    if _coast_tree is not None:
        return _coast_tree, _coast_polys
    with _coast_lock:
        if _coast_tree is not None:
            return _coast_tree, _coast_polys
        import cartopy.feature as cfeature
        from shapely.strtree import STRtree

        polys = []
        for g in cfeature.NaturalEarthFeature("physical", "land", "10m").geometries():
            if g.geom_type == "Polygon":
                polys.append(g)
            else:
                polys.extend(list(g.geoms))
        _coast_polys = polys
        _coast_tree = STRtree(polys)
    return _coast_tree, _coast_polys


def land_mask(lon2d, lat2d):
    """Boolean land mask for a tile, or None if the tile contains no land at all.

    Queries the coastline STRtree with the tile's bbox: no candidates -> open ocean
    (skip the point-in-polygon test entirely); otherwise test only the local polygons.
    """
    try:
        import shapely
        from shapely.geometry import box

        tree, polys = _coastline()
        lon = ((lon2d + 180.0) % 360.0) - 180.0
        idx = tree.query(
            box(
                float(lon.min()),
                float(lat2d.min()),
                float(lon.max()),
                float(lat2d.max()),
            )
        )
        if len(idx) == 0:
            return None
        local = shapely.union_all([polys[i] for i in np.asarray(idx)])
        return np.asarray(shapely.contains_xy(local, lon, lat2d), dtype=bool)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Coastline mask unavailable ({exc!r}); land not masked")
        return None


# --------------------------------------------------------------------------- #
# Dataset bake: fieldstore now-hour field -> dense global wave-height grid
# --------------------------------------------------------------------------- #
def bake_field(field0):
    """Bake the significant-wave-height grid from a fieldstore waves field (no GRIB).

    The collector stores the swell as a vector — u = swh*sin(dir), v = swh*cos(dir) — so
    the wave height is simply the vector magnitude: sqrt(u^2 + v^2) == swh exactly. This
    reads u/v straight from the fieldstore field, so the tile path no longer touches the
    GRIB cache or cfgrib. Bad/land cells (NaN) are nearest-neighbour filled so tiles never
    have holes. Grid layout comes from the field's own lat/lon, so meta describes it
    correctly regardless of lon convention (the sampler is wrap-agnostic).
    """
    from scipy.ndimage import distance_transform_edt

    lat = np.asarray(field0["lat"], dtype=np.float64)
    lon = np.asarray(field0["lon"], dtype=np.float64)
    u = np.asarray(field0["u"], dtype=np.float32)
    v = np.asarray(field0["v"], dtype=np.float32)
    swh = np.hypot(u, v).astype(np.float32)

    bad = ~np.isfinite(swh) | (swh < 0.0) | (swh > 60.0)
    if bad.all():
        raise ValueError("No valid wave data in fieldstore field")
    idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
    field = swh[tuple(idx)].astype(np.float32)
    meta = {
        "lat0": float(lat[0]),
        "dlat": float(lat[1] - lat[0]),
        "lon0": float(lon[0]),
        "dlon": float(lon[1] - lon[0]),
        "nlat": int(field.shape[0]),
        "nlon": int(field.shape[1]),
    }
    return field, meta


# --------------------------------------------------------------------------- #
# Versioning + published-state helpers
# --------------------------------------------------------------------------- #
def _tiles_root(config):
    workdir = config.get_setting("common", "workdir", ".")
    return os.path.join(workdir, "data", "waves_cache_tiles")


def _published_path(config):
    return os.path.join(_tiles_root(config), "published.json")


def _settings(config):
    waves = config.get_section("waves")
    palette = waves.get("palette", "ocean_storm")
    alpha255 = max(0, min(255, int(round(float(waves.get("alpha", 70)) / 100.0 * 255))))
    try:
        threshold = max(0.0, float(waves.get("min_wave_height", 0) or 0))
    except (TypeError, ValueError):
        threshold = 0.0
    return palette, alpha255, threshold


def current_version(config, run_date_str, run_id, fhour):
    """Identity of the *tiles* the live data + config imply — the rebuild trigger.

    Built from the fieldstore field's identity (run date / run / forecast hour — which
    changes whenever the collector ingests a newer run, or the now-hour advances within a
    run) plus ONLY the settings that change tile pixels: palette, alpha, min_wave_height.
    Every other waves setting (key_fontsize, runs_per_day, arrow_*, level_of_detail,
    prebuild_maxzoom, ...) is intentionally excluded, so editing those never forces a tile
    rebuild. Also used as the frontend tile cache-buster.
    """
    waves = config.get_section("waves")
    raw = (
        f"{run_date_str}|{run_id}|{int(fhour):03d}"
        f"|{waves.get('palette', 'ocean_storm')}"
        f"|{waves.get('alpha', 70)}|{waves.get('min_wave_height', 0)}"
    )
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def published_info(config):
    path = _published_path(config)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def published_version(config):
    info = published_info(config)
    return info.get("version") if info else None


# --------------------------------------------------------------------------- #
# Render helpers
# --------------------------------------------------------------------------- #
def _write_png(rgba, path):
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, path)
    return buf.getvalue()


def _compose_and_write(path, field, meta, lut, alpha255, threshold, z, x, y):
    """Render a tile and write it (even when fully transparent, so 'missing' always
    means 'not yet built' for the on-demand path)."""
    rgba = compose_tile_rgba(field, meta, lut, alpha255, threshold, z, x, y)
    return _write_png(rgba, path)


# --------------------------------------------------------------------------- #
# Builder side: publish immediately, then warm the base pyramid
# --------------------------------------------------------------------------- #
def publish_dataset(config, field0, run_date_str, run_id, fhour):
    """Bake the field and PUBLISH the new version immediately (before any tiles).

    Takes the fieldstore now-hour waves field (not a GRIB path). Returns
    (version, field, meta). After this returns, the API can already serve the new version
    on demand. Superseded version directories are pruned.
    """
    version = current_version(config, run_date_str, run_id, fhour)
    root = _tiles_root(config)
    vdir = os.path.join(root, version)
    os.makedirs(vdir, exist_ok=True)

    field, meta = bake_field(field0)
    np.save(os.path.join(vdir, "field.npy"), field)
    with open(os.path.join(vdir, "meta.json"), "w") as fh:
        json.dump(meta, fh)

    prebuild = int(
        config.get_section("waves").get("prebuild_maxzoom", PREBUILD_MAXZOOM_DEFAULT)
    )
    pub = {
        "version": version,
        "prebuilt_maxzoom": prebuild,
        "maxzoom": ONDEMAND_MAXZOOM,
        "tileSize": TILE_PX,
    }
    tmp = _published_path(config) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(pub, fh)
    os.replace(tmp, _published_path(config))

    for name in os.listdir(root):
        full = os.path.join(root, name)
        if name != version and os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
    _field_mem.clear()
    return version, field, meta


def warm_pyramid(config, version, field, meta, max_workers=None):
    """Render the base pyramid z0..prebuild_maxzoom into the published version dir.

    Low zoom first (the globe fills in almost immediately), skipping tiles that the
    on-demand path already produced. Runs in the builder; the API keeps serving
    on demand throughout.
    """
    palette, alpha255, threshold = _settings(config)
    lut = build_lut(palette)
    prebuild = int(
        config.get_section("waves").get("prebuild_maxzoom", PREBUILD_MAXZOOM_DEFAULT)
    )
    vdir = os.path.join(_tiles_root(config), version)

    _coastline()  # build the index once, before the worker threads fan out

    tasks = [
        (z, x, y)
        for z in range(0, prebuild + 1)  # low zoom first
        for x in range(2**z)
        for y in range(2**z)
    ]

    def warm_one(t):
        z, x, y = t
        path = os.path.join(vdir, str(z), str(x), f"{y}.png")
        if os.path.exists(path):
            return 0
        _compose_and_write(path, field, meta, lut, alpha255, threshold, z, x, y)
        return 1

    workers = max_workers or (os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        warmed = sum(ex.map(warm_one, tasks))
    logger.info(
        f"Waves tiles: warmed {warmed}/{len(tasks)} tiles (z0-z{prebuild}) "
        f"for version {version}"
    )


# --------------------------------------------------------------------------- #
# API side: serve a published tile, rendering ANY missing tile on demand
# --------------------------------------------------------------------------- #
def _load_field(config, version):
    if version in _field_mem:
        return _field_mem[version]
    vdir = os.path.join(_tiles_root(config), version)
    fp, mp = os.path.join(vdir, "field.npy"), os.path.join(vdir, "meta.json")
    if not (os.path.exists(fp) and os.path.exists(mp)):
        return None
    field = np.load(fp)
    with open(mp) as fh:
        meta = json.load(fh)
    _field_mem.clear()
    _field_mem[version] = (field, meta)
    return field, meta


def serve_tile(config, z, x, y):
    """Return PNG bytes for the published tile, rendering+caching it on demand if the
    builder hasn't warmed it yet. None only if nothing is published / no field on disk.
    """
    info = published_info(config)
    if not info:
        return None
    version = info["version"]
    path = os.path.join(_tiles_root(config), version, str(z), str(x), f"{y}.png")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()

    loaded = _load_field(config, version)
    if loaded is None:
        return None
    field, meta = loaded
    palette, alpha255, threshold = _settings(config)
    return _compose_and_write(
        path, field, meta, build_lut(palette), alpha255, threshold, z, x, y
    )
