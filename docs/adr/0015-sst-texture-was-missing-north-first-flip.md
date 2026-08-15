# Restore north-first row order for SST's data texture

Follow-on to `docs/adr/0014-dilate-sst-land-mask-for-linear-filtering-bleed.md`.
After that coastal-bleed fix shipped, the user reported the SST layer's
landmass mask as "upside-down" -- a distinct, more severe bug: the whole
layer's geography was mirrored across the equator, not just fringing at
coastlines.

## Root cause

`ui/modules/_webglfill.js`'s vertex shader documents its contract explicitly:
`v_uv` carries `y in [0,1] lat north->south` -- **row 0 of every
`encode_frames`-driven data texture must be the north pole**.

`Updater.regrid_for_lod()` (`tasks/common.py`), shared by every LOD-regridded
layer, always returns **ascending** latitude rows
(`new_lats = arange(lats.min(), lats.max()+step, step)`) regardless of the
input's own order -- i.e. row 0 = south pole.

Every other caller either avoids the mismatch or corrects it:

- `tasks/scalar_field.py` (temperature, isobars, ozone, stormwatch, pwat)
  encodes the texture from the RAW, un-regridded field (native cfgrib order,
  already north-first for these GFS products), not `regrid_for_lod`'s output
  -- `regrid_for_lod` is only used there for the separate contourf PNG.
- `tasks/precipitation.py`'s `_smooth_global_field` explicitly flips back
  after its own ascending-order interpolation pass, with the comment
  *"restore north-first row order for the texture"*.
- `lib/unpack.py`'s `wind_data_unpack`/`jetstream_data_unpack` enforce
  north-first via `ds.sortby("latitude", ascending=False)` before their own
  (differently-encoded, `encode_uv`) texture is built.

`tasks/sst.py`'s hand-rolled `plot()` passed `regrid_for_lod`'s ascending
output straight to `encode_frames` with no restore step -- so the entire SST
texture, data and land mask alike, rendered vertically mirrored: what should
render at the Arctic showed Antarctic-latitude data and vice versa. This is
what made the land mask itself look "upside-down": the mask was internally
consistent with the (also mirrored) data, so masking-versus-data pixel
comparisons in ADR-0014's investigation didn't catch it -- only checking
against real-world geography orientation would have.

## Fix

`SSTUpdater.plot()` now flips `new_lats`/`display_data` back to north-first
immediately after `regrid_for_lod()` returns, before anything else (land
mask, anomaly stats, encoding) touches them:

```python
new_lats, new_lons, display_data = self.regrid_for_lod(...)
new_lats = new_lats[::-1]
display_data = display_data[::-1, :]
mesh_lon, mesh_lat = np.meshgrid(new_lons, new_lats)
```

`coastline_land_mask()`'s rasterizer (`lib/coastline.py`) builds its affine
transform directly from the mesh's lat/lon step, sign included -- it already
runs unmodified with descending-latitude input for currents/waves (which get
north-first data from `wind_data_unpack`'s explicit sort), so no change was
needed there.

Verified live: restarted `layer_builder`, forced a fresh SST render, and
confirmed by eye in the browser that landmass now lines up correctly
(Antarctica in the south, Arctic in the north) instead of mirrored.

## `greenhouse_gases.py` has the identical latent bug, not yet fixed

`GhgUpdater.plot()` follows the exact same pattern -- `regrid_for_lod()`
straight into `encode_frames()`, no north-first restore -- so it is exposed
to the same mirroring if its source netCDF's native latitude happens to be
ascending (not confirmed either way here; out of scope for this fix, since
it wasn't the reported symptom). Revisit if greenhouse gases is ever reported
showing the same upside-down landmass.

## Revisit if

- `greenhouse_gases.py` is reported with mirrored geography -- apply the same
  restore-north-first flip immediately after its `regrid_for_lod()` call.
- Any future layer adds a hand-rolled `plot()` that calls `regrid_for_lod()`
  directly for its `encode_frames` texture (bypassing `scalar_field.py`'s
  shared path) -- it needs this same restore; worth promoting into
  `regrid_for_lod()` itself (an optional `north_first=True` return) if a
  third caller ever needs it, rather than trusting each new caller to
  remember the convention.
