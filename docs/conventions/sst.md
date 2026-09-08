# Worked example: Static fill layer (sst)

SST (sea-surface temperature) is one of three **Static fill layer** instances (see
[layers.md](layers.md)), alongside `greenhouse_gases` and `flood_risk` — six
`createStaticFillLayer` call sites total across those three frontend modules (two variants
each: SST's absolute/anomaly, GHG's species×mode, Flood Risk's Live/Historical).

## What makes it "static"

Unlike an Animated fill layer, there's no forecast-hour dimension — no scrubber, no
per-hour texture series. `SSTUpdater.run()` (`src/atmos_gl/tasks/sst.py`) renders once per
cycle and republishes to one fixed output path each time it's stale, the same "render
everything, publish only what's selected" shape `GhgUpdater` uses for its species/mode
combinations.

## Backend: fixed encode domain, live display range

`SSTUpdater.plot()` encodes the WebGL texture at a **fixed physical domain**
(`_ABS_ENCODE_VMIN`/`_ABS_ENCODE_VMAX`, or the anomaly pair) — never the user's live
min/max settings. That's deliberate (issue #312): the palette and the live display range
are applied entirely client-side, reading this fixed, generous domain, so a palette or
scale change never needs a server re-render. The one thing that *is* data-dependent —
anomaly mode's auto-scaled range — is written to a small JSON sidecar (`sst_meta.json`) for
the frontend's client-side LUT to read; `greenhouse_gases.py`'s `ghg_meta.json` is the same
pattern for the same problem.

SST also masks land: `coastline_land_mask()` cuts every land cell to NaN before encoding
(this is a sea-surface field — land has no meaningful value). Fire Risk is the inverse
(masks to land-only vegetation); GHG masks nothing (CO2/CH4 are well-mixed everywhere,
land and ocean alike) — three different masking answers for three different physical
domains, not one shared default.

`SSTUpdater.plot()` asks `regrid_for_lod(..., north_first=True)` — the GPU fill shader's
texture always needs row 0 = north pole, and this was in fact the layer where that bug was
first found (see `docs/adr/0015-sst-texture-was-missing-north-first-flip.md`); the flip
itself now lives inside `regrid_for_lod` rather than being every caller's own
responsibility.

## Frontend: `createStaticFillLayer`

`createStaticFillLayer` (`ui/modules/_webglfill.js`) shares its mesh-building
(`buildFillMesh`) and vertex shader (`VS_BODY`) with `createFillLayer` — the seam-tiling
geometry doesn't depend on how many textures a fill variant samples — but polls a single
texture on a refresh timer instead of stepping through an hour sequence driven by the
shared timeline.

## Where to look next

- [temperature.md](temperature.md) — the Animated fill layer sibling of this same GPU
  fill family
- CONTEXT.md's "Flood Risk" entry — the third Static fill layer, with two independently
  sourced modes sharing one section
