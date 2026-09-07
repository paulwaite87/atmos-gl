#!/usr/bin/env python3
"""Shared rasterio reproject(max) mechanics for a single-band categorical GeoTIFF --
used by lib/flood_risk.py (JRC hazard tiles, MODIS flood tiles) and
lib/vegetation_mask.py (MODIS Land Cover / IGBP classification), three unrelated
data sources that all reduce to "reproject one categorical band onto an arbitrary
destination grid, worst-case (max) resampled." Extracted out of lib/flood_risk.py
(architecture review candidate "move categorical-raster reprojection out of the
Flood Risk module") once vegetation_mask.py became a second, genuinely unrelated
caller -- nothing here is flood/JRC-specific.
"""
import numpy as np

# Arbitrary "reasonable global grid step" fallback, only used when a destination
# axis has a single point and no step can be derived from it by differencing two
# points. No real caller hits this today -- every existing destination grid (JRC/
# MODIS mosaic windows, vegetation-mask render grids) has more than one point on
# both axes -- this exists purely so a single-point axis degrades to *some* answer
# rather than raising ZeroDivisionError/IndexError.
_FALLBACK_STEP_DEG = 0.05


def reproject_categorical_max(
    tile_path: str, dst_lat, dst_lon, remap, *, max_source_pixels: int | None = None
) -> np.ndarray:
    """Read band 1 of a single-band categorical GeoTIFF, apply `remap(source_array,
    src_dataset)` (each caller's own nodata-zeroing / reclassification rule) to the
    SOURCE array BEFORE reprojecting, then max-resample onto the given destination
    cell-center axes -- typically the exact sub-window covering one tile's 10x10deg
    footprint (see lib/flood_risk.py's tile_dst_window). Resampling.max, not
    average/nearest: categorical hazard/detection/classification data must never
    let a coarse working-resolution cell hide a known worst-case within it (same
    reasoning as coastline.py's _rasterize_land_mask uses exact rasterization for
    categorical land/sea data).

    max_source_pixels: confirmed live (not a hypothetical) that omitting this reads
    the WHOLE band 1 into memory before remap/reproject even run -- fine for JRC/
    MODIS-flood's ~10x10deg tiles (small enough to read wholesale), but the
    vegetation mask's source is one global ~86400x35849 mosaic (~3.1 billion
    pixels uncompressed); reading that wholesale OOM-killed the process at ~4GB RSS
    for even a modest 180x360 destination grid. When set and the source has more
    pixels than this, the source is read via a decimated `out_shape` (nearest,
    cheap) chosen so its resolution comfortably exceeds the destination's, rather
    than reading every native pixel just to immediately discard nearly all of them
    in the max-resample. This is a real accuracy/memory trade-off: a small
    burnable patch entirely within pixels skipped by decimation could be missed --
    acceptable here because the destination grids that pass this are themselves far
    coarser than the decimated read, and the whole mask is inherently a coarse
    sanity filter, not a precision boundary. Existing callers omit this and keep
    doing a full wholesale read (their tiles are always small enough for it to be
    correct and cheap) -- passing it is opt-in, so their behaviour is unchanged.

    Assumes dst_lat is north-first (descending) -- every existing caller's mosaic
    grid is built that way (see lib/flood_risk.py's build_jrc_mosaic_grid). A caller
    with an ascending axis must flip it before calling this and flip the result back
    afterward (see lib/vegetation_mask.py's burnable_vegetation_mask for why that's
    a real case, not a hypothetical one).

    Remapping happens in the SOURCE array rather than via GDAL's src_nodata/
    dst_nodata: confirmed live that GDAL's max/min/average-family resamplers only
    apply nodata masking to a destination cell that has SOME valid contributing
    source pixels -- a destination cell whose contributing source window is
    entirely nodata reprojects the raw nodata value through unmasked instead of
    yielding dst_nodata, silently turning "unclassified"/"insufficient data" into a
    spurious "worse-than-any-real-category" reading under max-resampling. Handling
    it in the source array sidesteps the bug entirely.
    """
    import rasterio
    from rasterio import Affine
    from rasterio.warp import Resampling, reproject

    step_lat = float(dst_lat[0] - dst_lat[1]) if len(dst_lat) > 1 else _FALLBACK_STEP_DEG
    step_lon = float(dst_lon[1] - dst_lon[0]) if len(dst_lon) > 1 else _FALLBACK_STEP_DEG
    dst_transform = Affine(
        step_lon, 0.0, float(dst_lon[0]) - step_lon / 2.0,
        0.0, -step_lat, float(dst_lat[0]) + step_lat / 2.0,
    )
    dst = np.zeros((len(dst_lat), len(dst_lon)), dtype=np.uint8)

    with rasterio.open(tile_path) as src:
        src_transform = src.transform
        src_pixels = src.width * src.height
        if max_source_pixels and src_pixels > max_source_pixels:
            scale = (src_pixels / max_source_pixels) ** 0.5
            out_width = max(1, int(src.width / scale))
            out_height = max(1, int(src.height / scale))
            band = src.read(
                1, out_shape=(out_height, out_width), resampling=Resampling.nearest
            )
            src_transform = src.transform * src.transform.scale(
                src.width / out_width, src.height / out_height
            )
        else:
            band = src.read(1)
        source = remap(band, src)
        reproject(
            source=source,
            destination=dst,
            src_transform=src_transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            resampling=Resampling.max,
        )
    return dst
