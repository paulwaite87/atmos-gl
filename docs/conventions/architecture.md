# Architecture & Repository Layout

Linked from [AGENTS.md](../../AGENTS.md).

## Repository layout

```
src/atmos_gl/               ← PYTHONPATH root (PYTHONPATH=/opt/project/src)
  collectors/               ← ALL data-collection code lives here
    base.py                 ← CollectorBase, AsyncCollectorBase
    field_base.py           ← FieldCollectorBase(CollectorBase), SingleFileFieldCollector,
                              CycleContext, drain_backfill()
    gfs_atmos.py            ← GfsAtmosCollector
    gfs_waves.py            ← GfsWavesCollector
    rtofs_currents.py       ← RtofsCurrentsCollector
    quakes.py               ← QuakeCollector(CollectorBase)
    storms.py               ← StormsCollector(CollectorBase)
    volcanoes.py            ← VolcanoesCollector(CollectorBase)
    satellites.py           ← SatellitesCollector(CollectorBase)
    sst.py                  ← SstCollector(CollectorBase)      — file-cache, not fieldstore
    clouds.py               ← CloudsCollector(CollectorBase)   — file-cache, not fieldstore
    shipping.py             ← ShippingCollector(AsyncCollectorBase)
    lightning.py            ← LightningCollector(AsyncCollectorBase)
    markers_sync.py         ← MarkersSyncCollector(CollectorBase)
    service.py               ← CollectorService (run-loop + registry)
  ...
```

**The Dockerfile copies only `src/`.** Any Python file placed outside `src/` is
unreachable inside the container and will silently fail. Never create collector code
at the repo root.

**No `data_collector.py` shim exists.** `docker-compose.yml`'s `data_collector` service
invokes `CollectorService` directly (`python -m atmos_gl.collectors.service`), so nothing
depends on a shim module at runtime. `pyproject.toml`'s `[project.scripts]` no longer
declares a `datacollector` entry point (it pointed at this now-nonexistent module).

## Core architectural invariants

These are non-negotiable. Do not break them.

### 1. Collection is unconditional of frontend `enabled` flags

The backend must collect and store data regardless of whether a map layer is toggled
on in the frontend. When a user enables a layer, the data must already be present.

- `collect_event_feeds()` runs all five sync event-feed collectors with **no `enabled`
  gate whatsoever**. If you see `if self.enabled:` wrapping a call in that loop, remove it.
- `ShippingCollector` and `LightningCollector` retain `enabled` kill-switches *inside
  their own `run()` loops only*, as a rate-limit recovery mechanism during development.
  This is a deliberate exception, not a pattern to copy.

### 2. All orchestration lives in `collectors/service.py`

`CollectorService` is the single orchestrator — scheduling, supervision, and the
full/backfill cadences. There is no separate `data_collector.py` shim (see the repository
layout note above); don't reintroduce orchestration logic outside `service.py`.

### 3. GFS collectors share one NOMADS baseline probe per cycle

`GfsAtmosCollector` and `GfsWavesCollector` both need the GFS run baseline. They must
resolve it **once** via `CycleContext.baseline("gfs")`, which memoises the result for
the cycle. Never have each collector independently probe NOMADS — that doubles network
round-trips and risks them rendering different runs.

### 4. Shipping and lightning run as supervised asyncio tasks in-process

They are *not* separate Docker services. The `_supervise_collector()` wrapper in
`CollectorService` restarts them after a 30-second backoff on crash. API keys
(`AIS_API_KEY`, `OPENWEATHER_API_KEY`) are environment variables on the
`data_collector` Docker service only.

### 5. Package path is `src/atmos_gl/collectors/`

Imports must be `from atmos_gl.collectors.xyz import ...`, never relative imports
from a root-level `collectors/` directory.

## Collector class hierarchy

```
CollectorBase(ABC)                       # sync, periodic; base.py
  QuakeCollector
  StormsCollector
  VolcanoesCollector
  SatellitesCollector
  MarkersSyncCollector
  SstCollector                           # file-cache (data/*.nc), not fieldstore; sst.py
  CloudsCollector                        # file-cache (data/*.png), not fieldstore; clouds.py
  FieldCollectorBase(CollectorBase)      # adds CycleContext, fieldstore helpers; field_base.py
    GfsAtmosCollector
    GfsWavesCollector
    RtofsCurrentsCollector

AsyncCollectorBase(ABC)                  # persistent async; base.py
  ShippingCollector
  LightningCollector
```

`CollectorBase` provides:
- `section`, `enabled`, `period_s`, `is_stale()`, `has_new_data()`
- ETag/mtime caching via `_head_changed()`
- A standard `main()` entry point

When adding a new periodic source, subclass `CollectorBase` (or `FieldCollectorBase`
for forecast-field sources). Adding a new async source: subclass `AsyncCollectorBase`
and register it in `CollectorService._supervise_collector()`.

## Phase 3 — complete

The legacy `FieldIngest` monolith (`collectors/field_ingest.py`) has been decomposed into
three per-source `FieldCollectorBase` subclasses and deleted:

| File | Class | Baseline key | Datasource key |
|------|-------|-------------|----------------|
| `collectors/gfs_atmos.py` | `GfsAtmosCollector` | `"gfs"` | `"gfs"` |
| `collectors/gfs_waves.py` | `GfsWavesCollector` | `"gfs"` | `"gfs"` |
| `collectors/rtofs_currents.py` | `RtofsCurrentsCollector` | `"rtofs"` | `"currents"` |

`CycleContext` (in `field_base.py`) resolves and memoises each model baseline once per
cycle — `CollectorService._collect_fields()` constructs one `CycleContext` per full-refresh
pass and shares it across all three collectors, so the GFS pair shares a single NOMADS probe.

Demand-driven backfill also moved: each subclass implements `backfill_hour()` (plus the
shared `products` registry and `_valid_time()` on `FieldCollectorBase`), and
`field_base.drain_backfill(config, db, store, collector_classes)` is the generic dispatcher
`CollectorService.run()` calls each poll — the `FieldCollectorBase` counterpart to
`collectors/__init__.py`'s `_drive()`.

`GfsWavesCollector` and `RtofsCurrentsCollector` further share a concrete `collect()`/
`backfill_hour()` via `SingleFileFieldCollector(FieldCollectorBase)` (also in
`field_base.py`) — both fetch one whole file per forecast hour for a single product,
differing only in URL resolution/fallback (`_resolve_download_url()`), tempfile suffix,
and (RTOFS only) an f072 window cap/abort (`_expected_fhour_end()`/`_guard_cycle()`).
`GfsAtmosCollector`'s multi-product byte-range fetch is a genuinely different shape
(two axes of variance at once, not one) and stays its own `FieldCollectorBase`
subclass — see [Working philosophy](philosophy.md)'s "Deepening Template-Method
Hierarchies" section for why the split was drawn there.

Adding a fourth field source is now "one file + one registry entry": if it fetches one
whole file per forecast hour for a single product, subclass `SingleFileFieldCollector`
and implement `resolve_baseline()`/`_resolve_download_url()`, set `products`/
`tempfile_suffix` (override `_expected_fhour_end()`/`_guard_cycle()` only if the source
needs to cap or abort its window). Otherwise — multi-product, byte-range, or any other
fetch shape — subclass `FieldCollectorBase` directly and implement `resolve_baseline()`/
`collect(ctx)`/`backfill_hour()` in full, as `GfsAtmosCollector` does. Either way, add
the class to `service.py`'s `_FIELD_COLLECTOR_CLASSES` list.
