# Domain context — atmos-gl

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

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Single-hour scalar updater** | `SingleHourScalarUpdater` (`tasks/single_hour_scalar.py`), the shared base behind `ScalarFieldUpdater` and `PrecipitationUpdater` — owns the constructor fields and `run()` wiring (`get_gfs_state()` warm-up → key-refresh hook → `render_all_hours()`) genuinely identical between the two. `plot()` stays a full per-subclass override, not hook-split: the two classes' static-render color models and texture pipelines diverge too much (precipitation's discrete banded palette + smoothing/floor/sqrt-transform pipeline vs. the scalar-field trio's plain `Normalize`/threshold `cmap`) for splitting `plot()` into sub-hooks to pay off — see the module's own docstring. | scalar renderer base |

## Backend collectors

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Single-file field collector** | A `FieldCollectorBase` subclass (`SingleFileFieldCollector`, `collectors/field_base.py`) that fetches one whole file per forecast hour for a single product — `GfsWavesCollector`, `RtofsCurrentsCollector` — sharing one `collect()`/`backfill_hour()` implementation behind `_resolve_download_url()`/`_guard_cycle()` hooks. Distinct from `GfsAtmosCollector`'s multi-product byte-range fetch, which stays its own implementation, subclassing `FieldCollectorBase` directly. | multi-file collector |
| **Collector driver** | `CollectorDriver` (`collectors/driving.py`), the shared gate/try/record envelope behind `EventFeedDriver` (event feeds + file caches — `is_stale`/`has_new_data`/`collect`, own `last_runs` timestamp bookkeeping) and `FieldCollectorDriver` (the three `FieldCollectorBase` subclasses — unconditional `collect(ctx)`, freshness handled internally per forecast hour, a shared `CycleContext`). `_drive_one()` stays a full per-subclass override, not hook-split further: construction arity, whether there's an external freshness pre-check, and what per-cycle state gets threaded through are real domain differences between the two families, not incidental duplication — see the module's own docstring and `docs/adr/0001-dont-unify-gfs-rtofs-baseline-probing.md` for the same reasoning applied to a different pair in this package. | driver base |

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
