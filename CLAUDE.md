# CLAUDE.md — atmos-gl

Conventions and architectural invariants for AI-assisted work on this repository.
Read this before making any changes.

---

## Project overview

`atmos-gl` is a web-based global map with a **MapLibre GL JS v5** globe frontend
and a **Python 3.12 / FastAPI / PostGIS / Docker** backend. The production image is
`ghcr.io/paulwaite87/atmos-gl:latest`.

Key backend responsibilities:
- Periodic ingestion of GFS atmospheric, GFS wave, and RTOFS current forecast fields
- Event-feed collection (earthquakes, tropical storms, volcanoes, satellites)
- Shipping AIS and lightning strike ingestion (async, rate-limit-aware)
- Marker sync from a local GeoJSON file (`markers/markers.geojson`)
- PostGIS storage; FastAPI serves tiles and GeoJSON to the frontend

---

## What Not To Look At

Any files matching .gitignore entries should never be read, modified or otherwise manipulated in any way.

## Secrets

All API keys, or other secrets should never be committed to remote CVS.

---

## Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## Surgical Changes

**Touch only what you must. Clean up only your own mess by default, but ask if other messes should be as well.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## Deepening Template-Method Hierarchies

**When a base class already owns control flow and lets subclasses override hooks,
extend those hooks — don't pull newly discovered duplication out as a narrow,
free-standing helper sitting alongside them.**

If a class hierarchy already uses the template-method pattern (a base class defining
the control flow, subclasses overriding hook methods — e.g. `FieldCollectorBase`'s
`resolve_baseline()`, `_expected_fhour_end()`, `backfill_hour()`), and you find
duplication in the surrounding control flow across subclasses, lift that control flow
into the base class too, exposing whatever varies as another override hook. Don't
extract just the innermost duplicated body as a standalone helper and leave two
near-identical control-flow shells sitting in the subclasses either side of it.

Two ~90%-identical loop/control-flow bodies in sibling subclasses are harder to audit
for drift than one control-flow body in the base class with each subclass's override
list visible at a glance — the override list *is* the domain-difference
documentation. A narrow helper extraction also tends to leave existing duplication
bugs in place rather than surfacing them: if a subclass already recomputes a value
independently in two places (once in an existing hook, once inline in the method
you're deepening), pulling that method's control flow into the base forces it through
the existing hook instead, fixing the duplication as a side effect.

This only applies where template-method structure already governs the modules in
question. Where no such structure exists — a handful of leaf modules just doing
similar-looking things — a narrow, local extraction (or no extraction at all) is the
right call; don't invent a base class/hook hierarchy from scratch to justify one.
See `docs/adr/0002-dont-extend-hoverpopup-for-markers.md` for that other case.

## Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

---

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
    service.py              ← CollectorService (run-loop + registry)
  ...
```

**The Dockerfile copies only `src/`.** Any Python file placed outside `src/` is
unreachable inside the container and will silently fail. Never create collector code
at the repo root.

**No `data_collector.py` shim exists.** `docker-compose.yml`'s `data_collector` service
invokes `CollectorService` directly (`python -m atmos_gl.collectors.service`), so nothing
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

### 5. Package path is `src/atmos_gl/collectors/`

Imports must be `from atmos_gl.collectors.xyz import ...`, never relative imports
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

## CodeGraph

If CodeGraph is installed (a `.codegraph/` index directory exists at the repo root and
the MCP server is available), use it before grep/find or reading files when you need to
understand or locate code — `codegraph_explore` (or `codegraph explore "<query>"` from
the shell) returns the relevant symbols' verbatim source plus call paths in one call.
It's optional tooling, not a project dependency — if it isn't installed, skip it
entirely and fall back to normal search/read.

Some sessions may also have local `PreToolUse` hooks in `.claude/settings.json`
enforcing this preference (e.g. blocking `Grep`, nudging away from delegating to a
file-reading subagent) — those are personal, untracked config, since this repo's
`.gitignore` excludes `.claude/` entirely. Don't assume every contributor or session has
them.

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

`GfsWavesCollector` and `RtofsCurrentsCollector` further share a concrete `collect()`/
`backfill_hour()` via `SingleFileFieldCollector(FieldCollectorBase)` (also in
`field_base.py`) — both fetch one whole file per forecast hour for a single product,
differing only in URL resolution/fallback (`_resolve_download_url()`), tempfile suffix,
and (RTOFS only) an f072 window cap/abort (`_expected_fhour_end()`/`_guard_cycle()`).
`GfsAtmosCollector`'s multi-product byte-range fetch is a genuinely different shape
(two axes of variance at once, not one) and stays its own `FieldCollectorBase`
subclass — see "Deepening Template-Method Hierarchies" above for why the split was
drawn there.

Adding a fourth field source is now "one file + one registry entry": if it fetches one
whole file per forecast hour for a single product, subclass `SingleFileFieldCollector`
and implement `resolve_baseline()`/`_resolve_download_url()`, set `products`/
`tempfile_suffix` (override `_expected_fhour_end()`/`_guard_cycle()` only if the source
needs to cap or abort its window). Otherwise — multi-product, byte-range, or any other
fetch shape — subclass `FieldCollectorBase` directly and implement `resolve_baseline()`/
`collect(ctx)`/`backfill_hour()` in full, as `GfsAtmosCollector` does. Either way, add
the class to `service.py`'s `_FIELD_COLLECTOR_CLASSES` list.

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

- Production image: `ghcr.io/paulwaite87/atmos-gl:latest`
- `PYTHONPATH=/opt/project/src` in the container
- The Dockerfile copies only `src/` — anything outside is invisible at runtime
- Service-level env vars (API keys, DB credentials) live in `docker-compose.yml`
  under the relevant service, not in a global `.env` unless shared across services
- `AIS_API_KEY` and `OPENWEATHER_API_KEY` belong on the `data_collector` service

---

## Settings changes

`config/atmos-gl.json` is gitignored — it's the **live** config the running stack actually
reads and writes (via the config UI or hand-edits), and it drifts constantly during normal
use. `config/atmos-gl.json.tmpl` is the **tracked template**, committed to the repo, and is
the one Claude Code should read when it needs to know the current *shape* of the config
schema (section/option names, structure) for something like a code change.

When a task involves refactoring settings (adding/renaming/restructuring config
sections or options — e.g. the PWAT layer's config additions, or the `ozone`/`pwat`
critical-palette fields):

1. Modify and test the change against the live `config/atmos-gl.json` first — this is
   where iteration happens, exactly like any other manual settings change.
2. Once the shape has settled, update `config/atmos-gl.json.tmpl` to match it exactly.
3. Commit `config/atmos-gl.json.tmpl` alongside whatever other code changes are part of
   that refactor. Never commit `config/atmos-gl.json` itself (it's gitignored — don't
   force-add it).

A fresh checkout has no live `config/atmos-gl.json` at all — only the `.tmpl`. `make
up`/`make prod` bootstrap it automatically (see the Makefile's `bootstrap-config`
target: copies the template over if the live file is missing, never overwrites an
existing one). CI does the same as an explicit step before running pytest. Anything
that reads config outside those paths (a one-off script, a fresh test run) needs that
copy to exist first.

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

Issues are tracked in this repo's GitHub Issues (paulwaite87/atmos-gl), via the `gh` CLI. External PRs are not treated as a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) — no repo-specific remapping. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Prioritized skills

The broader skill catalog under `~/.agents/skills/` is symlinked in globally, but these
are the ones actively in use for this repo:

- `tdd` — test-driven development; build features or fix bugs test-first
- `code-review` — review changes since a fixed point along Standards and Spec axes
- `wayfinder` — plan and track work too large for one session as a map of tickets
- `diagnosing-bugs` — diagnosis loop for hard bugs and performance regressions
- `to-issues` — break a plan/PRD into independently-gradable GitHub issues
- `to-prd` — turn a conversation into a PRD and publish it to GitHub
- `triage` — move incoming issues/PRs through the triage label state machine
- `implement` — implement a piece of work from a PRD or set of issues
- `improve-codebase-architecture` — scan for deepening opportunities, report, then grill through one
- `ubiquitous-language` — extract a domain glossary into `CONTEXT.md`
- `handoff` — compact the current conversation into a handoff doc for another agent
- `claude-handoff` — hand the current conversation to a fresh background agent
- `grilling` — grill the user relentlessly about a plan or design before building
- `grill-me` — a relentless interview to sharpen a plan or design
- `domain-modeling` — build and sharpen the project's domain model, record ADRs
- `prototype` — build a throwaway prototype to answer a design question