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

## Update: `greenhouse_gases.py` hit the predicted bug, and the flip is now promoted

The "revisit if" below wasn't hypothetical: `GhgUpdater.plot()` shipped the
identical bug (reported live as the land mask registering over the wrong
hemisphere -- see PR #393, which also removed GHG's land mask entirely as a
separate, unrelated decision -- CO2/CH4 are well-mixed atmospheric properties
with no land/ocean distinction to cut). That made GHG the third real
occurrence this ADR's own "revisit if" anticipated, so the flip is now a
`north_first: bool` parameter on `regrid_for_lod()` itself (architecture
review candidate "consolidate the north-first flip that SST and GHG both
re-derived") -- both `SSTUpdater.plot()` and `GhgUpdater.plot()` now pass
`north_first=True` instead of flipping `new_lats`/`display_data` by hand
afterward; the fix's own code snippet above reflects the pre-promotion shape
kept for historical context.

Confirmed still scoped narrowly, not a blanket fix for every `regrid_for_lod`
caller: `air_quality.py` independently needs north-first too but reaches it a
third way (`imshow(origin="lower")`, a different render pipeline entirely,
not `encode_frames`) -- a `north_first` parameter doesn't reach it. `wind.py`/
`scalar_field.py`/`precipitation.py`'s own regrid never see this problem
(contourf is orientation-agnostic, or they encode from the raw native-order
field instead). See `tests/test_common_regrid_for_lod.py` for the parameter's
own tests.

## Revisit if

- A third `encode_frames`-via-`regrid_for_lod` caller appears with yet
  another orientation quirk `north_first: bool` doesn't cover -- the
  parameter's contract (flip both `new_lats` and `field_smooth` together,
  default `False`) should stay this simple unless a real caller needs
  something the boolean can't express.
