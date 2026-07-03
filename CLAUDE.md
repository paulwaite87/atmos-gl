# CLAUDE.md — worldmap-ng

Conventions and architectural invariants for AI-assisted work on this repository.
Read this before making any changes.

---

## Project overview

`worldmap-ng` is a web-based global map with a **MapLibre GL JS v5** globe frontend
and a **Python 3.12 / FastAPI / PostGIS / Docker** backend. The production image is
`ghcr.io/paulwaite87/worldmap-ng:latest`.

Key backend responsibilities:
- Periodic ingestion of GFS atmospheric, GFS wave, and RTOFS current forecast fields
- Event-feed collection (earthquakes, tropical storms, volcanoes, satellites)
- Shipping AIS and lightning strike ingestion (async, rate-limit-aware)
- Marker sync from a local GeoJSON file (`markers/markers.geojson`)
- PostGIS storage; FastAPI serves tiles and GeoJSON to the frontend

---

## Repository layout

```
src/worldmap/               ← PYTHONPATH root (PYTHONPATH=/opt/project/src)
  collectors/               ← ALL data-collection code lives here
    base.py                 ← CollectorBase, AsyncCollectorBase
    field_base.py           ← FieldCollectorBase(CollectorBase), CycleContext, drain_backfill()
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
    service.py              ← CollectorService (run-loop + registry)
  ...
```

**The Dockerfile copies only `src/`.** Any Python file placed outside `src/` is
unreachable inside the container and will silently fail. Never create collector code
at the repo root.

**No `data_collector.py` shim exists.** `docker-compose.yml`'s `data_collector` service
invokes `CollectorService` directly (`python -m worldmap.collectors.service`), so nothing
depends on a shim module at runtime. `pyproject.toml`'s `[project.scripts]` no longer
declares a `datacollector` entry point (it pointed at this now-nonexistent module).

---

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

### 5. Package path is `src/worldmap/collectors/`

Imports must be `from worldmap.collectors.xyz import ...`, never relative imports
from a root-level `collectors/` directory.

---

## Tooling

- **Python**: 3.12
- **Package manager**: `uv` exclusively. Never suggest `pip install`; always `uv add`
  or `uv run`. Virtual environments are managed by `uv`.
- **IDE**: PyCharm. `.idea/` is excluded from git — never stage or commit it.
- **Docker Compose** drives all services. Changes to service structure belong in
  `docker-compose.yml`, not in ad-hoc `docker run` commands.
- **Makefile**: project-specific targets for common workflows (build, up, logs, test).

---

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

---

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

Adding a fourth field source is now "one file + one registry entry": subclass
`FieldCollectorBase`, implement `resolve_baseline()`/`collect(ctx)`/`backfill_hour()`, set
`products`, and add the class to `service.py`'s `_FIELD_COLLECTOR_CLASSES` list.

---

## Validation and testing approach

Heavy dependencies (`cfgrib`, `psycopg2`, NOMADS HTTP) are not available in the
dev environment without a running stack. Follow this approach for any new or modified
file:

1. **`ast.parse` validation** — every generated Python file must parse cleanly.
   Include a shebang comment noting it was validated.
2. **Stub harness** — provide a lightweight smoke test using `unittest.mock` stubs
   for `psycopg2`, `cfgrib`, and HTTP calls so structure can be verified without
   the full stack.
3. **Stage incrementally** — deliver one logical unit at a time (e.g., one collector
   class), confirm it before proceeding to the next.
4. **Confirm assumptions before large implementations** — if the right approach is
   unclear, ask before writing a substantial block of code.

---

## Docker conventions

- Production image: `ghcr.io/paulwaite87/worldmap-ng:latest`
- `PYTHONPATH=/opt/project/src` in the container
- The Dockerfile copies only `src/` — anything outside is invisible at runtime
- Service-level env vars (API keys, DB credentials) live in `docker-compose.yml`
  under the relevant service, not in a global `.env` unless shared across services
- `AIS_API_KEY` and `OPENWEATHER_API_KEY` belong on the `data_collector` service

---

## Git workflow

- Work on a **feature branch** named `feature/<short-description>` or
  `fix/<short-description>`, branched from `master` (this repo's main branch is `master`,
  not `main`).
- Commits should be atomic and described in the imperative mood
  (`Add GfsAtmosCollector`, `Fix enabled gate in collect_event_feeds`).
- Open a pull request against `master` when the branch is ready for review.
- Never commit `.idea/`, `__pycache__/`, `*.pyc`, or any generated output that
  belongs in `.gitignore`.

---

## Things to avoid

- **Don't use `pip`** — always `uv`.
- **Don't add orchestration logic outside `collectors/service.py`.**
- **Don't gate collection on `enabled` flags** (except in shipping/lightning `run()` loops).
- **Don't create files outside `src/`** expecting them to be importable in Docker.
- **Don't have multiple collectors independently probe NOMADS for the same baseline.**
- **Don't stage `.idea/`** or any PyCharm-specific files.
- **Don't make large structural changes without confirming the approach first.**

---

## Agent skills

### Issue tracker

Issues are tracked in this repo's GitHub Issues (paulwaite87/worldmap-ng), via the `gh` CLI. External PRs are not treated as a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) — no repo-specific remapping. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.