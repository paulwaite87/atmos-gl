# Dilate SST's land mask by one cell to stop coastal colour bleed

Reported symptom: the SST layer appeared to render coloration over land. The
server-side data and mask were the first suspects.

## Investigation

Pulled the live-rendered `sst_absolute.png` texture and NOAA's raw OISST netCDF
out of the running `layer_builder` container and checked them directly:

- Sampled known interior-land points (Sahara, central Australia, Amazon,
  Mongolia, Kansas) against the rendered texture's alpha channel -- all
  correctly masked (`alpha=0`).
- Rebuilt `coastline_land_mask()`'s own mask at the render's exact grid and
  diffed it pixel-for-pixel against the rendered texture's alpha channel:
  **99.89% agreement**, with the ~0.1% mismatch concentrated at coastline
  edges, not scattered inland.

Conclusion: `tasks/sst.py`'s land cut (GSHHG `'h'`-resolution coastline, see
`docs/adr/0013-switch-coastline-masking-to-gshhg.md`) is accurate. The bug is
not in the data or the mask -- it's in how the GPU samples the encoded
texture.

## Root cause: LINEAR filtering + hard alpha discard

`ui/modules/_webglfill.js` uploads the encoded data texture (NaN-over-land =
`alpha=0`, everything else `alpha=255`, from `lib/texture.py`'s
`encode_frames`) with `TEXTURE_MIN_FILTER`/`TEXTURE_MAG_FILTER = gl.LINEAR`,
and the fragment shader does a hard cutoff: `if (d.a < 0.5) discard;`
(identical pattern in both `createFillLayer` and `createStaticFillLayer`).

LINEAR filtering interpolates the alpha channel continuously across every
texel boundary, including the true coastline edge. A fragment sampling
partway between an ocean texel (`alpha=255`) and a land texel (`alpha=0`) can
land anywhere in between; anything landing >= 0.5 survives the discard, with
RGB *also* blended between the ocean colour and the land texel's colour
(zeroed by `encode_frames`'s `nan_to_num`). That produces a fading colour
fringe bleeding up to half a texel (~4-5km at SST's 0.08 deg render grid)
onto the land side of every coastline.

This shader pattern is used by every fill layer (temperature, isobars,
precipitation, wind, ...), not just SST, but only layers with a genuine
land/sea alpha discontinuity in their data can show it. Atmospheric fields
render over land and ocean alike (alpha=255 everywhere, no boundary to
blend across); SST is a solid heatmap fill with a real coastline
discontinuity and a vivid palette, making the artifact visible. The same
latent bleed likely affects currents/waves at their own coastlines, not
addressed here.

## Fix: dilate the land mask by one grid cell before cutting

In `SSTUpdater.plot()`, `land` is dilated (`scipy.ndimage.binary_dilation`,
full 8-connectivity `structure=np.ones((3,3))`) by one cell before
`display_data[land] = np.nan`. This pushes the hard NaN boundary one texel
further offshore, so the LINEAR blend zone stays entirely within
already-masked territory and can never produce a passing (colored) alpha on
the land side. Cost: a ~9km-wide sliver of true coastal water goes uncolored
too -- imperceptible at the zoom levels this layer renders at, and a clean
trade against "never colors land."

8-connectivity, not scipy's 4-connectivity default: bilinear filtering
samples a 2x2 texel neighbourhood, including the diagonal, so a land cell
only diagonally touching open ocean is still exposed to the blend. Confirmed
live -- 4-connectivity dilation left a residual ~0.002% of true land cells
still rendering colour (down from ~0.03% pre-fix, but not zero), all at
diagonal-only coastal corners; switching to 8-connectivity closed it.

## Considered options

- **Switch the data texture to NEAREST filtering** -- rejected: MIN/MAG
  filters apply uniformly, not just at the land/sea boundary, so this would
  make the entire open-ocean gradient blocky at typical zoom levels, not
  just fix the coastline.
- **Decouple alpha into a second, NEAREST-filtered mask texture, sampled
  separately from the LINEAR-filtered colour/value texture** -- the
  "architecturally correct" fix, but adds a second texture, sampler uniform,
  and shader plumbing to every masked layer for a coastal artifact that a
  one-cell mask dilation already eliminates. Revisit if a future masked
  layer needs the offshore band this trades away back.
- **Dilate the land mask by one cell before cutting** -- chosen. Minimal,
  scoped to `sst.py`, no shader/filtering changes, verified live (unit test
  `test_plot_masks_land_cells_as_nan_before_encoding` updated to assert the
  dilated boundary).

## Revisit if

- Currents or waves are reported showing the same coastal bleed -- same
  fix (dilate before cut) applies at their `LandMaskCache` call sites in
  `tasks/currents.py`/`tasks/waves.py`.
- A future layer needs the true coastline crisp right up to the geometry's
  edge (no offshore give at all) -- that needs the decoupled-mask-texture
  approach above, not another dilation.
