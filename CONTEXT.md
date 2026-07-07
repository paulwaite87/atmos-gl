# Domain context — worldmap-ng

Domain language for the map's data layers. Extend as terms are sharpened; keep
definitions to one sentence.

## Layers

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Scalar field** | A layer rendered as a single-scalar `contourf` heatmap over a value range — temperature, ozone, and stormwatch (CAPE) — sharing one renderer (`ScalarFieldUpdater`) and differing only by a `ScalarFieldSpec` (colormap, range, `extend`, key ticks, title). | scalar layer, heatmap layer |

A **Scalar field** is distinct from the vector layers (wind, currents), the
boundary/level layers (isobars, precipitation's `BoundaryNorm`), and SST's
runtime-computed range — those do not share the scalar-field renderer or its spec
shape.

## Data conventions

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Direction convention (FROM)** | Wave/wind direction fields (GRIB `dirpw`/`mwd`) are WMO convention: the angle a flow arrives FROM, not the heading it travels TOWARD. Deriving a travel vector requires negating: `u = -mag*sin(dir)`, `v = -mag*cos(dir)` (see `lib/unpack.py`'s `_swell_uv`). | heading, bearing |

Getting this backwards silently points every particle/vector layer 180° from its true
direction — a real bug (`waves_data_unpack`) lived exactly here before being fixed and
pinned by `tests/test_lib_unpack.py`.

## Backend render tasks

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **ForecastState** | The (run_date, run_id, forecast_hour) triple a render call operates on (`tasks/common.py`), passed explicitly everywhere — never cached as mutable instance state. Built via `Updater.get_gfs_state()`/`get_rtofs_state()` (the shared per-cycle baseline) or `ForecastState.at_hour(run_date, run_id, fhour)` (a specific catalog hour). | run state, forecast context |

Every `Updater`/`MultiHourRenderMixin` method that needs to know "which run, which
hour" takes a `ForecastState` parameter rather than reading `self`. Before this, the
four raw attributes it replaced (`run_date_str`/`run_id`/`forecast_hour_str`) were
mutated directly on `self`, forcing `hasattr` guards and two separate save/restore
`try`/`finally` dances to avoid callers clobbering each other's state.
