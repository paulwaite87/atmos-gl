# Worked example: markers (the one-instance hybrid)

`markers` is the fifth shape (see [layers.md](layers.md)) and the only layer that has it —
not a reusable pattern, just its own case. It sits between the other four shapes: it has a
real backend task like an Animated/Static fill layer, but that task never renders an
image — it only enriches DB rows the frontend reads back exactly like a Point-feed layer.

## Two independent backend pieces

**`MarkersSyncCollector`** (`src/atmos_gl/collectors/markers_sync.py`) is a collector like
[quakes.md](quakes.md)'s — but its "remote feed" is a local file: it keeps the DB `markers`
table in sync with the canonical `markers/markers.geojson`, upserting every feature and
deleting rows no longer present. This owns the *structural* data (name, kind, country,
lat/lon) — the marker definitions themselves.

**`MarkerUpdater`** (`src/atmos_gl/tasks/markers.py`) *is* a `TASK_CLASSES["markers"]`
entry — so it runs in the render pool alongside every fill-layer `Updater` — but its
`run()` has no `plot()` and produces no PNG or texture. Instead, when
`markers.weather_popup` is on, it bilinearly samples the GFS fields already ingested for
other layers (temperature, wind, humidity) at each place marker's coordinates and writes
the results straight into the `Marker` row's `wx_*` columns (`wx_temp_c`,
`wx_wind_ms`, ...). This is a periodic DB-enrichment job wearing a render task's clothes.

## Frontend: consumed like a Point-feed layer

The frontend reads everything — static marker definitions and current weather alike —
from one DB-backed GeoJSON route (`/api/markers/geojson`), same shape as any Point-feed
layer's live read. `markers.js` uses `_hoverpopup.js` for its weather popup (the same
shared pipeline [quakes.md](quakes.md) describes) and `keepLayersOnTop`
(`ui/modules/_layerstack.js`, shared with `landmass.js`) to keep marker symbols drawn above
the fill layers beneath them.

## Why this isn't its own shape

A shape name is useful for grouping instances that share real structure other layers will
also need. Markers is a category of one — its split (sync collector + weather-sampling
task, both feeding one DB-consumed feed) is specific to place markers needing live weather
without a dedicated external API. There's no second instance to generalize from, so it's
documented here as its own worked example rather than promoted to a named shape in
[layers.md](layers.md)'s table.

## Where to look next

- [quakes.md](quakes.md) — the pure Point-feed shape markers' frontend half matches
- [temperature.md](temperature.md) — the pure render-task shape markers' backend half
  superficially resembles (a `TASK_CLASSES` entry), but doesn't actually follow
