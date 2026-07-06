#!/usr/bin/env python3
"""GPU texture encoders for the raster/particle layers (architecture review candidate
"slim Updater; texture encoding home"). encode_frames/encode_uv are general
numpy-array-to-PNG encoders with no Updater/tasks-specific dependency -- they lived in
tasks/common.py only because that's where the layers calling them also lived. Moved
here, alongside the other cross-cutting, no-single-domain-owner modules in lib/
(config, logging, fieldstore, data_status).

Validated with ast.parse.
"""
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def encode_frames(frames, output_path, vmin, vmax, transform=None, bits=16):
    """
    Stack N scalar fields vertically into a single RGBA PNG, for upload as a
    WebGL2 2D-array texture (one array layer per frame, frame 0 on top).

    bits=16 (default): R = high byte, G = low byte of a 16-bit normalised value
      (65535 levels), B=0, A = mask. Decode on the GPU: norm = (R*256 + G)/65535.
      65535 levels eliminates the visible value-stepping that 8-bit (256 levels)
      causes — most obvious on thin contour lines (isobars), but it also removes
      faint banding in colour ramps. This is the default for all raster layers.
    bits=8: R = normalised value (0..1 -> 0..255), G=B=0, A = mask. Legacy/compact.

    transform:
      None    -> linear normalisation (m - vmin) / (vmax - vmin)
      'sqrt'  -> sqrt of the linear norm; gives the low end far more precision
                 (e.g. precipitation). Combines with 16-bit for even finer low end.
    Decode on the GPU as: value = norm (then square it for 'sqrt' layers).
    """
    span = float(vmax - vmin)
    slabs = []
    shape0 = None
    for m in frames:
        m = np.asarray(m, dtype=np.float32)
        if shape0 is None:
            shape0 = m.shape
        elif m.shape != shape0:
            raise ValueError(f"Frame shape mismatch: {m.shape} vs {shape0}")
        norm = np.clip((m - vmin) / span, 0.0, 1.0)
        if transform == "sqrt":
            norm = np.sqrt(norm)
        norm = np.nan_to_num(norm, nan=0.0)  # NaN -> 0 (masked out via alpha)
        a = np.where(np.isnan(m), 0, 255).astype(np.uint8)
        if bits == 16:
            q = np.clip(np.round(norm * 65535.0), 0, 65535).astype(np.uint32)
            hi = (q >> 8).astype(np.uint8)  # R = high byte
            lo = (q & 0xFF).astype(np.uint8)  # G = low byte
            z = np.zeros_like(hi)
            slabs.append(np.dstack((hi, lo, z, a)))
        else:
            r = (norm * 255.0).astype(np.uint8)
            z = np.zeros_like(r)
            slabs.append(np.dstack((r, z, z, a)))
    filmstrip = np.vstack(slabs)  # (N*H, W, 4)
    Image.fromarray(filmstrip, mode="RGBA").save(output_path, format="PNG")
    logger.debug(
        f"Saved {len(frames)}-frame data texture ({bits}-bit) to {output_path} {filmstrip.shape}"
    )
    return True


def encode_uv(u, v, output_path, vmax, lat=None):
    """
    Encode a global vector field (U=east, V=north, in m/s) into a single RGBA PNG
    for a GPU particle layer:  R = (U + vmax) / (2*vmax),  G = (V + vmax) / (2*vmax),
    B = 0,  A = 255 (0 where NaN).  Row 0 = north, lon -180..180.
    Decode on the GPU as:  component = channel * (2*vmax) - vmax.
    vmax clips extremes; pick it a little above the strongest winds you care about.

    The particle shader's toMerc() maps the top texture row to +90 lat and treats G as
    the true northward component, so the texture MUST be north-at-top. cfgrib does not
    guarantee a row order (it can hand back latitude ascending = south-first depending on
    the GRIB), and unpack/_standardize_lon only normalises longitude. If south-first rows
    reach here, the field is encoded vertically mirrored: every particle samples the wrong
    hemisphere AND the (un-negated) V is inconsistent with the flipped geometry, which turns
    rotation into divergence — highs render as radial outflow instead of circulating.
    Passing `lat` lets this self-orient: if lat is ascending we flip the rows to north-first
    so the output is correct regardless of what cfgrib produced. lat and u/v are guaranteed
    consistent here (they come from the same fieldstore .npz).
    """
    u = np.asarray(u, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    if u.shape != v.shape:
        raise ValueError(f"U/V shape mismatch: {u.shape} vs {v.shape}")
    # Guarantee north-at-top. If latitude runs south->north (ascending), flip the rows.
    if lat is not None:
        lat = np.asarray(lat)
        if lat.ndim == 1 and lat.size >= 2 and float(lat[0]) < float(lat[-1]):
            u = u[::-1]
            v = v[::-1]
    span = 2.0 * float(vmax)
    mask = np.isnan(u) | np.isnan(v)
    ru = np.clip((np.nan_to_num(u) + vmax) / span, 0.0, 1.0)
    rv = np.clip((np.nan_to_num(v) + vmax) / span, 0.0, 1.0)
    r = (ru * 255.0).astype(np.uint8)
    g = (rv * 255.0).astype(np.uint8)
    z = np.zeros_like(r)
    a = np.where(mask, 0, 255).astype(np.uint8)
    img = np.dstack((r, g, z, a))
    Image.fromarray(img, mode="RGBA").save(output_path, format="PNG")
    logger.debug(f"Saved wind vector texture to {output_path} {img.shape}")
    return True
