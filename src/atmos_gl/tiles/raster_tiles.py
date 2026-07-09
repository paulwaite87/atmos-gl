#!/usr/bin/env python3
"""Generic raster-tile engine: bake a dense global scalar field (from the fieldstore) and
serve 256x256 Web-Mercator PNG tiles for it, palette-LUT'd by value.

The machinery here is field-agnostic — it knows nothing about waves, temperature, ozone,
etc. specifically. Per-layer differences (value range, colour ramp, land masking, which
fieldstore slot holds the scalar, which config keys drive palette/alpha/threshold) are
captured in a TileSpec; the engine threads that spec through bake/version/publish/serve.

Lifecycle per layer:
  * The layer builder resolves the now-hour fieldstore field and calls publish_dataset,
    which bakes the global field, writes field.npy + meta.json into a versioned dir, and
    PUBLISHES (published.json) immediately so the API can serve on demand.
  * warm_pyramid then renders the base pyramid (low zoom first) in the background.
  * The API (routes/tiles.py) serves a published tile, rendering ANY missing tile on
    demand. `version` changes when the data or the pixel-affecting settings change, which
    busts the frontend tile cache.
"""

import os
import io
import json
import shutil
import hashlib
import logging
import threading
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

import numpy as np

logger = logging.getLogger("atmos_gl.tiles.raster_tiles")

LAT_LIMIT = 85.0511
TILE_PX = 256
PREBUILD_MAXZOOM_DEFAULT = 6  # base pyramid depth warmed on refresh
ONDEMAND_MAXZOOM = 9  # real tiles served to here (deeper still renders)

_lut_cache: dict[str, np.ndarray] = {}
_field_mem: dict[tuple, tuple] = {}  # (section, version) -> (field, meta), API-side
_coast_tree = None
_coast_polys = None
_coast_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Per-layer specification
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TileSpec:
    """Everything the generic engine needs to know about ONE layer.

    Colour source is EITHER a matplotlib named colormap (cmap_name) OR a palette registry
    of RGB stops (palettes + palette_setting/default_palette). value_key is the fieldstore
    slot holding the scalar; hypot_fallback derives it from sqrt(u^2+v^2) when that slot is
    absent (waves back-compat). clip_lo/clip_hi are validity bounds for the nearest-fill.

    """

    section: str  # config section, tiles-root dirname, fieldstore product
    vmin: float = 0.0
    vmax: float = 1.0
    mask_land: bool = False  # True only for ocean-only fields (waves, sst)
    value_key: str = "values"
    hypot_fallback: bool = False
    clip_lo: float = -1.0e30
    clip_hi: float = 1.0e30
    cmap_name: str | None = None
    palettes: dict | None = None
    default_palette: str | None = None
    palette_setting: str | None = None
    alpha_setting: str = "alpha"
    alpha_default: float = 70.0
    threshold_setting: str | None = None


# --------------------------------------------------------------------------- #
# Tile geometry / palette / sampling (pure; testable without cartopy)
# --------------------------------------------------------------------------- #
def tile_pixel_lonlat(z: int, x: int, y: int, px: int = TILE_PX):
    n = 2.0**z
    col = (np.arange(px) + 0.5) / px
    lon = (x + col) / n * 360.0 - 180.0
    row = (np.arange(px) + 0.5) / px
    merc = np.pi * (1.0 - 2.0 * (y + row) / n)
    lat = np.degrees(np.arctan(np.sinh(merc)))
    return lon, lat


def _palette_id(spec: TileSpec, config) -> str:
    """The colour identifier that actually selects pixels: the config-chosen palette for a
    palette-registry layer, else the fixed matplotlib cmap name."""
    if spec.palette_setting:
        sect = config.get_section(spec.section)
        return sect.get(spec.palette_setting, spec.default_palette) or spec.default_palette
    return spec.cmap_name or spec.default_palette or spec.section


def build_lut(spec: TileSpec, palette_id: str) -> np.ndarray:
    key = f"{spec.section}:{palette_id}"
    if key in _lut_cache:
        return _lut_cache[key]
    import matplotlib.cm as cm
    import matplotlib.colors as mcolors

    if spec.palettes is not None:
        rgb = spec.palettes.get(palette_id) or next(iter(spec.palettes.values()))
        cmap = mcolors.LinearSegmentedColormap.from_list(spec.section, rgb, N=256)
    else:
        cmap = cm.get_cmap(spec.cmap_name)

    lut = (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255.0).astype(np.uint8)
    _lut_cache[key] = lut
    return lut


def sample_field(field, meta, lon2d, lat2d):
    from scipy.ndimage import map_coordinates

    col = ((lon2d - meta["lon0"]) / meta["dlon"]) % meta["nlon"]
    row = np.clip((lat2d - meta["lat0"]) / meta["dlat"], 0, meta["nlat"] - 1)
    return map_coordinates(field, [row, col], order=1, mode="grid-wrap", prefilter=False)


def compose_tile_rgba(
    spec, field, meta, lut, alpha255, threshold, z, x, y, land_fn=None, px=TILE_PX
):
    lon, lat = tile_pixel_lonlat(z, x, y, px)
    lon2d, lat2d = np.meshgrid(lon, lat)
    val = sample_field(field, meta, lon2d, lat2d)

    span = spec.vmax - spec.vmin
    idx = (np.clip((val - spec.vmin) / span, 0.0, 1.0) * 255.0).astype(np.uint8)
    rgb = lut[idx]

    alpha = np.full(val.shape, alpha255, dtype=np.uint8)
    if threshold is not None and threshold > spec.vmin:
        alpha[val < threshold] = 0
    if spec.mask_land:
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
# Dataset bake: fieldstore now-hour field -> dense global scalar grid
# --------------------------------------------------------------------------- #
def bake_field(spec: TileSpec, field0):
    """Bake the dense global scalar grid for this layer from a fieldstore field (no GRIB).

    Reads the scalar from field0[spec.value_key] (e.g. 'values'); for waves, the swell is
    stored as a vector, so hypot_fallback derives the height as sqrt(u^2+v^2) == swh when
    the scalar slot is absent (back-compat with fields stored before the unpacker wrote it).
    Bad/out-of-range cells (NaN) are nearest-neighbour filled so tiles never have holes.
    Grid layout comes from the field's own lat/lon, so meta describes it correctly
    regardless of lon convention (the sampler is wrap-agnostic).
    """
    from scipy.ndimage import distance_transform_edt

    lat = np.asarray(field0["lat"], dtype=np.float64)
    lon = np.asarray(field0["lon"], dtype=np.float64)

    val = field0.get(spec.value_key)
    if val is None and spec.hypot_fallback:
        val = np.hypot(
            np.asarray(field0["u"], dtype=np.float32),
            np.asarray(field0["v"], dtype=np.float32),
        )
    if val is None:
        raise ValueError(f"{spec.section}: field has no '{spec.value_key}' slot")
    val = np.asarray(val, dtype=np.float32)

    bad = ~np.isfinite(val) | (val < spec.clip_lo) | (val > spec.clip_hi)
    if bad.all():
        raise ValueError(f"No valid {spec.section} data in fieldstore field")
    idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
    field = val[tuple(idx)].astype(np.float32)
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
def _tiles_root(spec: TileSpec, config):
    workdir = config.get_setting("common", "workdir", ".")
    return os.path.join(workdir, "data", f"{spec.section}_cache_tiles")


def _published_path(spec: TileSpec, config):
    return os.path.join(_tiles_root(spec, config), "published.json")


def _settings(spec: TileSpec, config):
    """Return (palette_id, alpha255, threshold) from the layer's live config section."""
    sect = config.get_section(spec.section)
    palette_id = _palette_id(spec, config)
    alpha255 = max(
        0,
        min(
            255,
            int(round(float(sect.get(spec.alpha_setting, spec.alpha_default)) / 100.0 * 255)),
        ),
    )
    threshold = None
    if spec.threshold_setting:
        try:
            threshold = max(spec.vmin, float(sect.get(spec.threshold_setting, 0) or 0))
        except (TypeError, ValueError):
            threshold = None
    return palette_id, alpha255, threshold


def current_version(spec: TileSpec, config, run_date_str, run_id, fhour):
    """Identity of the *tiles* the live data + config imply — the rebuild trigger.

    Built from the fieldstore field's identity (run date / run / forecast hour — which
    changes whenever the collector ingests a newer run, or the now-hour advances) plus ONLY
    the inputs that change tile pixels: the colour ramp, alpha, threshold, and value range.
    Unrelated settings (key_fontsize, runs_per_day, ...) are excluded, so editing those
    never forces a rebuild. Also the frontend tile cache-buster.
    """
    palette_id, alpha255, threshold = _settings(spec, config)
    raw = (
        f"{spec.section}|{run_date_str}|{run_id}|{int(fhour):03d}"
        f"|{palette_id}|{alpha255}|{threshold}|{spec.vmin}|{spec.vmax}"
    )
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def published_info(spec: TileSpec, config):
    path = _published_path(spec, config)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def published_version(spec: TileSpec, config):
    info = published_info(spec, config)
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


def _compose_and_write(spec, path, field, meta, lut, alpha255, threshold, z, x, y):
    """Render a tile and write it (even when fully transparent, so 'missing' always
    means 'not yet built' for the on-demand path)."""
    rgba = compose_tile_rgba(spec, field, meta, lut, alpha255, threshold, z, x, y)
    return _write_png(rgba, path)


# --------------------------------------------------------------------------- #
# Builder side: publish immediately, then warm the base pyramid
# --------------------------------------------------------------------------- #
def publish_dataset(spec: TileSpec, config, field0, run_date_str, run_id, fhour):
    """Bake the field and PUBLISH the new version immediately (before any tiles).

    Returns (version, field, meta). After this returns, the API can already serve the new
    version on demand. Superseded version directories are pruned.
    """
    version = current_version(spec, config, run_date_str, run_id, fhour)
    root = _tiles_root(spec, config)
    vdir = os.path.join(root, version)
    os.makedirs(vdir, exist_ok=True)

    field, meta = bake_field(spec, field0)
    np.save(os.path.join(vdir, "field.npy"), field)
    with open(os.path.join(vdir, "meta.json"), "w") as fh:
        json.dump(meta, fh)

    prebuild = int(
        config.get_section(spec.section).get(
            "prebuild_maxzoom", PREBUILD_MAXZOOM_DEFAULT
        )
    )
    pub = {
        "version": version,
        "prebuilt_maxzoom": prebuild,
        "maxzoom": ONDEMAND_MAXZOOM,
        "tileSize": TILE_PX,
    }
    tmp = _published_path(spec, config) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(pub, fh)
    os.replace(tmp, _published_path(spec, config))

    for name in os.listdir(root):
        full = os.path.join(root, name)
        if name != version and os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
    _field_mem.clear()
    return version, field, meta


def warm_pyramid(spec: TileSpec, config, version, field, meta, max_workers=None):
    """Render the base pyramid z0..prebuild_maxzoom into the published version dir.

    Low zoom first (the globe fills in almost immediately), skipping tiles that the
    on-demand path already produced. Runs in the builder; the API keeps serving on demand
    throughout.
    """
    palette_id, alpha255, threshold = _settings(spec, config)
    lut = build_lut(spec, palette_id)
    prebuild = int(
        config.get_section(spec.section).get(
            "prebuild_maxzoom", PREBUILD_MAXZOOM_DEFAULT
        )
    )
    vdir = os.path.join(_tiles_root(spec, config), version)

    if spec.mask_land:
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
        _compose_and_write(spec, path, field, meta, lut, alpha255, threshold, z, x, y)
        return 1

    workers = max_workers or (os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        warmed = sum(ex.map(warm_one, tasks))
    logger.info(
        f"{spec.section} tiles: warmed {warmed}/{len(tasks)} tiles (z0-z{prebuild}) "
        f"for version {version}"
    )


# --------------------------------------------------------------------------- #
# API side: serve a published tile, rendering ANY missing tile on demand
# --------------------------------------------------------------------------- #
def _load_field(spec: TileSpec, config, version):
    mem_key = (spec.section, version)
    if mem_key in _field_mem:
        return _field_mem[mem_key]
    vdir = os.path.join(_tiles_root(spec, config), version)
    fp, mp = os.path.join(vdir, "field.npy"), os.path.join(vdir, "meta.json")
    if not (os.path.exists(fp) and os.path.exists(mp)):
        return None
    field = np.load(fp)
    with open(mp) as fh:
        meta = json.load(fh)
    _field_mem.clear()
    _field_mem[mem_key] = (field, meta)
    return field, meta


def serve_tile(spec: TileSpec, config, z, x, y):
    """Return PNG bytes for the published tile, rendering+caching it on demand if the
    builder hasn't warmed it yet. None only if nothing is published / no field on disk.
    """
    info = published_info(spec, config)
    if not info:
        return None
    version = info["version"]
    path = os.path.join(_tiles_root(spec, config), version, str(z), str(x), f"{y}.png")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()

    loaded = _load_field(spec, config, version)
    if loaded is None:
        return None
    field, meta = loaded
    palette_id, alpha255, threshold = _settings(spec, config)
    return _compose_and_write(
        spec, path, field, meta, build_lut(spec, palette_id), alpha255, threshold, z, x, y
    )


# --------------------------------------------------------------------------- #
# Per-layer specs + registry
# --------------------------------------------------------------------------- #
# Wave-height palettes (single source of truth; waves.py imports these for its legend).
WAVES_PALETTES = {
    "ocean_storm": [
        (0.0, 0.2, 0.4),
        (0.0, 0.6, 0.3),
        (0.9, 0.7, 0.0),
        (0.8, 0.2, 0.0),
        (0.9, 0.9, 0.9),
    ],
    "neon_surge": [
        (0.0, 0.8, 1.0),
        (0.0, 0.95, 0.4),
        (1.0, 0.9, 0.0),
        (1.0, 0.3, 0.0),
        (0.9, 0.0, 0.5),
        (0.6, 0.0, 0.7),
    ],
    "solar_flare": [
        (0.6, 1.0, 0.9),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (1.0, 0.65, 0.0),
        (1.0, 0.2, 0.1),
        (1.0, 0.0, 1.0),
    ],
}

WAVES_SPEC = TileSpec(
    section="waves",
    vmin=0.0,
    vmax=8.0,
    mask_land=True,
    value_key="values",
    hypot_fallback=True,  # back-compat: derive swh from the stored swell vector
    clip_lo=0.0,
    clip_hi=60.0,
    palettes=WAVES_PALETTES,
    default_palette="ocean_storm",
    palette_setting="palette",
    alpha_setting="alpha",
    alpha_default=70.0,
    threshold_setting="min_wave_height",
)

# Registry keyed by section, so the API can serve any layer generically.
# Additional layers are added here as each is wired to tiles.
SPECS: dict[str, TileSpec] = {
    "waves": WAVES_SPEC,
}