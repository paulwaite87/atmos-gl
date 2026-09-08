# Worked example: Point-feed layer (quakes)

Quakes is one of 11 **Point-feed layer** instances (see [layers.md](layers.md)) — the
largest group of the five shapes. A Point-feed layer has **no server-side render task at
all**: no `TASK_CLASSES` entry, no `Updater` subclass, no PNG or texture. The layer is
just a collector keeping a DB table fresh, read back live as GeoJSON.

## Backend: collector only

`QuakeCollector` (`src/atmos_gl/collectors/quakes.py`) fetches the USGS earthquake summary
CSV (HEAD-checked first via `_head_changed_or_default`, so an unchanged file costs one
cheap request instead of a full download every cycle), filters by `min_mag`, and upserts
rows into the `earthquakes` table via `QuakeAdapter`. That's the entire backend — there is
no `"quakes"` key anywhere in `layer_builder.py`'s `TASK_CLASSES`, because there's nothing
to render.

This is the shape shared by volcanoes, world_events, troublespots, lightning, storms,
shipping, satellites, flightradar, terminator, and landmass — each a collector-only DB
sync, no render task, differing only in their data source and DB schema (see
`src/atmos_gl/db/models.py`'s `Earthquake`, `VolcanicActivity`, `Fire`, etc.).

## Frontend: `_feedhelpers.js` + `_hoverpopup.js`

The frontend reads live rows from a DB-backed GeoJSON API route (`/api/quakes`) rather than
any file the backend writes. Popups for every Point-feed layer (and `markers`, see
[markers.md](markers.md)) go through one shared pipeline:

- `buildPopupHtml` (`ui/modules/_feedhelpers.js`) — a typed `blocks` array (`divider`,
  `rows`, `line`, `emphasis`, `notice`, `fallback`) rather than raw HTML strings, so every
  popup layout stays expressible through one shared model. See CONTEXT.md's "Popup content
  block" and "Title variant" entries.
- `hoverPopup` (`ui/modules/_hoverpopup.js`) — show/hide/positioning, accepting a
  `layerId` (string or array), a configurable trigger event, and a live `enabled`
  predicate.

This "one-stop-shop" popup pipeline is itself a completed architecture-review candidate
(#6) that superseded `docs/adr/0002-dont-extend-hoverpopup-for-markers.md` — that ADR's
reasons for keeping markers separate were exactly what `hoverPopup` widened to cover.

## Where to look next

- [markers.md](markers.md) — a single-instance hybrid that's frontend-consumed the same
  way as a Point-feed layer, but has a real backend task behind it
- `ui/modules/_feedhelpers.js` — the shared popup content model
