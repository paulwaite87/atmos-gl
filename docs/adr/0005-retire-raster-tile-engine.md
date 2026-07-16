# Retire the generic raster tile engine

`tiles/raster_tiles.py` (server-side bake/publish/serve pipeline for 256x256
Web-Mercator PNG tiles, palette-LUT'd per pixel) and its route,
`routes/tiles.py` (`GET /api/tiles/{layer}/meta`, `GET
/api/tiles/{layer}/{z}/{x}/{y}.png`), were a generic engine keyed by a
per-layer `TileSpec` registry (`SPECS`). By the time of the `waves` layer's
migration to `createFillLayer` (the client-side GPU mesh shader every other
animated layer — wind, currents, precipitation, isobars, ozone, ... — already
used), `waves` was `SPECS`'s only remaining entry. That migration commit left
both files in place with `SPECS = {}`, deliberately un-registered rather than
deleted, so the decision to retire the subsystem outright could be made
separately once the migration's outcome was confirmed on `master`.

It has now been confirmed: the migration merged (PR #151) with no regressions,
and no other layer has picked up the tile engine in the meantime — every
animated layer in the app renders via `createFillLayer`.

## Considered Options

- **Keep the engine in place, unregistered, as a fallback for a future layer**
  — rejected. The one scenario that would call for it (live exact-geometry
  per-pixel masking with no server-side bake) hasn't materialized, and dead
  code with no registered caller and no test coverage protecting its
  correctness is a liability, not an asset — it bit-rots silently until
  someone needs it and discovers it's stale or broken.
- **Delete the engine outright** — chosen. `tiles/raster_tiles.py`,
  `routes/tiles.py`, the whole (now-empty) `tiles/` package, and their
  dedicated tests (`tests/test_raster_tiles.py`, `tests/test_tiles_route.py`)
  were removed; `api.py` no longer imports or registers `routes.tiles`.
  `WAVES_PALETTES`, the one constant `tasks/waves.py` still sourced from the
  engine's registry (`raster_tiles.WAVES_PALETTES`, to avoid a third copy of
  the literal), moved to live directly in `tasks/waves.py` as `PALETTES` —
  waves.py no longer imports anything from the deleted package.

## Revisit if

A future layer genuinely needs server-side per-pixel rendering that
`createFillLayer`'s client-side mesh-shader sampling can't express (e.g. a
render-time geometric operation too expensive or awkward to do in-shader). At
that point, write a new engine informed by what that layer actually needs
rather than reviving this one — `createFillLayer` has been the working
pattern for every layer since, and a generic engine designed for one future
unknown consumer tends to fit that consumer poorly anyway.
