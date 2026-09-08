# Worked example: Animated fill layer (temperature)

Temperature is the simplest instance of the **Animated fill layer** shape (see
[layers.md](layers.md)) — a plain, unthresholded scalar heatmap, with no particle overlay
and no bespoke masking. It's also the layer sharing `ScalarFieldUpdater` with ozone,
stormwatch, and pwat, so it doubles as the entry point into that shared renderer.

## The join-key string

`"temperature"` is threaded through, unenforced, in four places:

- `ui/index.html`'s `ALL_LAYERS` array (drives the dynamic `import('./modules/temperature.js')`)
- `ui/modules/temperature.js`'s `sectionKey` passed to `createFillLayer`
- `src/atmos_gl/layer_builder.py`'s `TASK_CLASSES["temperature"]`
- `config/atmos-gl.json`'s `"temperature"` section

## Backend: `ScalarFieldUpdater`

`TASK_CLASSES["temperature"]` (`src/atmos_gl/layer_builder.py`) is not the class directly —
it's `partial(ScalarFieldUpdater, spec=SPECS["temperature"])`. `ScalarFieldUpdater`
(`src/atmos_gl/tasks/scalar_field.py`) is the one renderer shared by four layers
(temperature, ozone, stormwatch, pwat), each supplying its own `ScalarFieldSpec` —
colormap, `vmin`/`vmax`, tick marks, and (for ozone/pwat only) a "critical zone" threshold
palette. Temperature's own spec:

```python
"temperature": ScalarFieldSpec(
    product="temperature", cmap="RdYlBu_r", vmin=-40.0, vmax=50.0,
    extend="both", ticks=[-40, -20, 0, 10, 20, 30, 40, 50], title="Temperature (°C)",
),
```

`ScalarFieldUpdater.plot()` runs once per forecast hour (driven by
`SingleHourScalarUpdater.run()` → `render_all_hours`) and produces **two independent
outputs** from the same regridded field:

1. A static `contourf` PNG (best-effort — a known Cartopy antimeridian bug can fail this
   without blocking output 2)
2. A raw WebGL data texture (`<hour>_data.png`, via `encode_frames`) — this is what the
   frontend's animated fill actually reads

Both go through `Updater.regrid_for_lod` and the spec's fixed `vmin`/`vmax` — never a live
per-render scale, so a palette/threshold setting change never needs a re-render (see
`_resolve_cmap()`'s live re-read of settings each call, for ozone/pwat's threshold path).

## Frontend: `createFillLayer`

`ui/modules/temperature.js` hardcodes its own mirror of the backend spec —
`VMIN=-40.0, VMAX=50.0, TICKS=[...]` — matching `SPECS["temperature"]` exactly but with no
code-level link between the two (a bug pattern the join-key fragility note in
[layers.md](layers.md) also applies to: a spec change on one side and not the other
silently desyncs the legend from the texture's actual encoded domain).

`createFillLayer` (`ui/modules/_webglfill.js`) is the shared GPU renderer behind all 20
Animated fill layer instances. It:

- Builds one shared tiled lon/lat mesh (`buildFillMesh`) — five world-copies wide so the
  globe's antimeridian seam never shows a gap at any zoom
- Samples consecutive-hour data textures on the GPU, driven by the shared `timeline`
  (see `scrubber.js`/`timeline.js`) — the scrubber's play/seek controls every Animated fill
  layer at once, not each layer independently
- Reads `sectionKey` ("temperature") to resolve which config settings (opacity, palette)
  apply

## Where to look next

- [sst.md](sst.md) — the same GPU fill mechanism, minus the forecast-hour animation
- [wind.md](wind.md) — an Animated fill layer that also drives a Particle overlay
- `src/atmos_gl/tasks/single_hour_scalar.py` — the shared base behind `ScalarFieldUpdater`
  and `PrecipitationUpdater`'s `run()` wiring (see CONTEXT.md's "Single-hour scalar updater")
