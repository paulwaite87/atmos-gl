# Layer anatomy

A "layer" is one entry in `ui/index.html`'s `ALL_LAYERS` array — a toggleable item in the
map's layer list, each dynamically `import()`-ed from its own `ui/modules/<name>.js`. There
are 28 of them, and despite the variety (weather fields, particle animation, live event
feeds, place markers), they only come in **five distinct shapes**. This page is the index;
each shape has a worked example in its own file, tracing one real layer end to end.

Read this first if you're new to the codebase and want to understand how a layer gets from a
data source to a pixel on the globe — then jump to whichever shape's example matches the
layer you're about to touch.

## The five shapes

| Shape | What it is | Frontend | Backend | Worked example |
| --- | --- | --- | --- | --- |
| **Animated fill layer** | A forecast-hour-animated GPU texture fill — the scrubber plays through consecutive hours. | `createFillLayer` (`ui/modules/_webglfill.js`) | A per-forecast-hour `Updater.plot()` (own renderer, or the shared `ScalarFieldUpdater`) | [temperature.md](temperature.md) |
| **Static fill layer** | A GPU texture fill with no forecast-hour dimension — one texture, poll-refreshed. | `createStaticFillLayer` (`ui/modules/_webglfill.js`) | A once-per-cycle `Updater.plot()` | [sst.md](sst.md) |
| **Particle overlay** | A GPU particle/streamline animation layered on top of an Animated fill layer's own vector field. | `_particles_gl.js` (oriented-quad) or `_streamparticles_gl.js` (streamline-ribbon) | Same backend as the fill layer it rides on | [wind.md](wind.md) |
| **Point-feed layer** | Discrete point/symbol features with no server-side render task at all — pure DB-backed GeoJSON. | `_feedhelpers.js` + `_hoverpopup.js` | A collector only; no `TASK_CLASSES` entry | [quakes.md](quakes.md) |
| **Markers** (one instance) | A hybrid: backend-enriched like a render task, but frontend-consumed as a plain point feed like shape 4. | `_layerstack.js` + `_hoverpopup.js` | `MarkerUpdater` — no image render, writes live weather into the DB row | [markers.md](markers.md) |

11 of the 28 `ALL_LAYERS` entries are Point-feed layers (quakes, volcanoes, world_events,
troublespots, lightning, storms, shipping, satellites, flightradar, terminator, landmass);
`markers` is the only layer of its shape. The rest split across the first three shapes, with
several — wind chief among them — combining more than one.

**"Animated fill layer" is broader than the "Scalar field" domain term.** Scalar field names
only the `ScalarFieldUpdater`-backed subset (temperature, ozone, stormwatch, pwat); isobars,
precipitation, wind, currents, waves, and jetstream are also Animated fill layers, each with
its own bespoke `plot()`.

## The join-key fragility

A single layer is threaded together by a **bare string repeated in four unenforced
places**: its `ALL_LAYERS` entry, the `sectionKey` passed to `createFillLayer`/
`createStaticFillLayer`, its `TASK_CLASSES` dict key (`src/atmos_gl/layer_builder.py`), and
its `config/atmos-gl.json` section name. Nothing in the code checks that these four strings
agree — a typo in any one silently detaches that piece from the other three (e.g. a config
section nothing reads, or a `TASK_CLASSES` entry no layer ever requests). This is a known,
accepted fragility, documented here rather than enforced in code — see each shape's example
for where the string actually gets threaded through.

## The underscore-prefix convention

In `ui/modules/`, a **bare filename is a layer entry point**, dynamically imported via
`ALL_LAYERS` (e.g. `temperature.js`, `quakes.js`). A **leading underscore marks an
internal/shared helper module** that's never itself in `ALL_LAYERS` (e.g. `_webglfill.js`,
`_feedhelpers.js`, `_hoverpopup.js`, `_layerstack.js`, `_particles_gl.js`).

**Exception**: `scrubber.js`, `timeline.js`, and `timeline_boot.js` are bare-named but are
not layer entry points. `timeline_boot.js` is imported directly by `index.html` as a
singleton bootstrap, outside the per-layer `ALL_LAYERS` loop, and it in turn imports the
other two — so none of the three go through the dynamic `import()` path this convention is
describing.
