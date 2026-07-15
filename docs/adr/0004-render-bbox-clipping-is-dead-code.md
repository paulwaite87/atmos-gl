# Render-time bbox clipping is dead code — regions are reporting-only

While diagnosing SST's pixelated coastline (`SSTUpdater.plot()` clipped OISST data to
`self.map_region_bbox` plus a 1° buffer before rendering, mirroring a pattern also
baked into `Updater.regrid_for_lod()`), it became clear this clipping is a vestige of
the old XPlanet desktop viewport model, where each region rendered as its own bounded
image. That model is gone: every layer in this app renders **globally** now — the
MapLibre globe frontend has no concept of a bounded viewport render, only tiles/textures
covering the whole world, panned and zoomed client-side.

`MapRegion` (the rendering-side dataclass in `tasks/common.py`, not `db/models.py`'s
SQLAlchemy model of the same name) and the `common.region` config setting still exist,
and still matter — but only for **reporting** (e.g. counting ships within a named
region's bbox), never for deciding what to render or how to clip it. Any bbox-based
*clipping* found in render code is dead: nothing renders to a sub-global bbox anymore,
so clipping to one only ever discarded data the viewer could otherwise pan/zoom to see,
and — in SST's case — actively caused visible pixelation by working from a needlessly
small, low-resolution slice.

## Considered Options

- **Keep bbox clipping "for performance"** — rejected. No render path is actually
  scoped to a sub-global view; the bbox in practice always evaluates to the world
  extent (`Plot.get_figure()`'s use of `region.bbox` for figure extent is unaffected —
  it's still a real value, just always world-view today). Clipping against a bbox that
  is always the whole world buys nothing and cost SST real coastline resolution.
- **Remove render-time bbox clipping, keep `MapRegion`/`common.region` for reporting
  only** — chosen. `Updater.regrid_for_lod()` dropped its `bbox` parameter and the
  clip step ahead of the LOD interpolation; `precipitation.py`/`wind.py`/
  `scalar_field.py`'s calls were updated to match. `SSTUpdater.plot()` dropped its own
  manual `lon_mask`/`lat_mask` clip and now regrids+renders the full global field. The
  now-unused `Updater.map_region_bbox` convenience attribute was removed.

## Revisit if

A genuinely regional render mode is reintroduced (e.g. a future non-globe embed view
scoped to one area) — at that point clipping earns its keep again, but should be
re-added at the specific call site that needs it, not restored globally into
`regrid_for_lod()`.
