---
status: supersedes 0009
---

# Flight Radar: database-backed cache-warming collector, REST-served, matching the shipping/quakes/markers shape

Supersedes `docs/adr/0009-flight-radar-region-keyed-push-architecture.md`. `0009`'s
region-keyed *scheduling* idea (bucket the globe into cells, poll the single
longest-waiting due cell each tick, never fire more than one request at once) survives
here, generalized from "cells a WebSocket viewport subscribed to" to the whole globe —
but "no DB, aircraft state entirely in memory" and "lives entirely inside `map_api`" do
not. Flight Radar now follows the same collector → Postgres → REST-GeoJSON shape every
other event-feed layer in this app already uses (quakes, storms, shipping, ...),
addressed by issue #215.

`0007`'s original "no collector, no persistence" design and `0009`'s WebSocket-push
fix-up both had the same underlying limitation: aircraft data only ever existed for
whatever a currently-open browser viewport asked adsb.lol about, live. There was no
"resting" global picture, and no history — no historical-track feature was possible for
aircraft the way one already exists for ships (`docs/adr/0007`'s own "Revisit if"
section anticipated this exact gap: "a genuine need emerges for aircraft data to
persist across sessions"). This ADR revisits that condition deliberately, alongside
`0009`'s rejection of a "global sweep of the whole planet" — the new design **is** a
global sweep, just a bounded, prioritized one rather than the unbounded 1-2s-cadence
version `0009` rejected.

## Decisions

- **A new `AircraftCollector` (`collectors/aircraft.py`, `AsyncCollectorBase`) is
  adsb.lol's sole consumer.** No other code path queries adsb.lol live — the WebSocket
  route, `RegionManager`, and `map_api`'s background poller are removed entirely.
  `AircraftCollector` runs embedded inside `CollectorService`/`data_collector`
  (`EMBEDDABLE_COLLECTORS`), supervised and restarted the same way as
  `ShippingCollector`/`LightningCollector` — not its own Docker service, not living
  alongside the request-serving `map_api` process the way `0009`'s poller did.
- **A fixed, explicit request-rate budget (`flightradar_collector.requests_per_minute`,
  default 6/minute) bounds adsb.lol load, deliberately** — carried over from the one
  empirical data point this codebase has (`0009`'s own hot-cadence default, the fastest
  cadence already confirmed live against adsb.lol without getting blocked). One shared
  budget covers both hotspot-priority and background-sweep sampling; which cell "wins"
  a given tick is entirely a function of the priority scoring below, not a separately
  carved-up sub-budget.
- **Scheduling is `GlobalSampleScheduler`** (`lib/flight_radar.py`), a pure,
  `now`-driven priority queue re-evaluated fresh after *every single* sample — never
  "sweep the world, then reconsider." It generalizes `0009`'s
  due/longest-waiting-first, one-region-per-tick shape (`RegionManager.next_due_region`)
  across two cell populations instead of one:
  - **Fine grid** (`FINE_GRID_DEG`, same 5° resolution as the old viewport hot-cell
    grid): whichever cells currently have an active viewer, sampled at a fast,
    unpenalized cadence.
  - **Coarse grid** (`COARSE_GRID_DEG`, 30° — 72 cells globally): everywhere else.
    Deliberately much coarser than the fine grid, not just for simplicity — a
    30-minute starvation floor at a 6/minute budget can cover at most 180 cells
    total; the fine grid's own 2,592 cells would need ~14x the budget (or a ~7-hour
    floor) to keep the same guarantee.
  - **A hard starvation floor** (`STARVATION_FLOOR_S`, 30 minutes) overrides both
    tiers' normal cadence once breached, guaranteeing the "global-at-a-glance" picture
    `0007` originally wanted — no cell goes unsampled indefinitely just because
    nothing prioritizes it.
  - **Adaptive, not static, background prioritization**: a coarse cell that keeps
    coming back empty gets its effective cadence stretched (capped, never fully
    excluded — the starvation floor still eventually rechecks it). No static
    airport/airway density reference table is built or maintained; "expected traffic
    density" is approximated by proximity-to-viewers plus this empirical filtering
    rather than external geographic data.
- **Viewer interest is DB-mediated, not a live subscription.** A small
  `aircraft_interest` table holds one row per active viewer/session (full viewport
  bounds, not just a center point — the fine-grid scheduling needs to know how big the
  viewport actually is, not just where its center sits), read fresh by
  `AircraftCollector` every cycle. A row not refreshed within a configured max age is
  treated as gone, the same grace-period idea `0009`'s subscriber-count-drops-to-zero
  had, just persisted across process boundaries instead of held in one connection
  handler's memory.
- **WebSocket is removed entirely, replaced by a REST endpoint**
  (`GET /api/flightradar/geojson`), the same shape `routes/shipping.py`'s
  `get_ships_geojson` already uses. The endpoint's bbox query parameters do double duty:
  they're both what gets returned (current aircraft as GeoJSON, read from the `aircraft`
  master table) *and* what gets upserted into `aircraft_interest` as a side effect —
  one request, not two mechanisms. This is possible specifically because freshness is
  no longer "however fast we can push a result we just fetched live" — it's bounded by
  the collector's own cadence regardless of transport, so there's nothing left for a
  persistent connection to buy.
- **Schema mirrors `ships`/`ship_position`**: an `Aircraft` master table (keyed on the
  ICAO 24-bit address, "hex" — ADR: not "AircraftPosition", the name originally
  proposed for the history table, once it was noticed `ship_position` itself already
  carries course/speed/status, not just position — `ship_position`'s own rename is a
  separate, out-of-scope future fix) carrying both static identity fields and a
  redundant current-variable-state snapshot (the same read-optimization trade-off
  `ships` already makes), plus `AircraftTrack`, pure append-only history.
- **The historical-track/trail feature ships from day one**, not deferred — config
  keys (`flightradar.view_tracks`/`track_limit`/`track_color`) and a
  `GET /api/flightradar/{hex}/track` route mirror shipping's hover-track exactly. This
  directly answers `0007`'s "Revisit if... a genuine need emerges for aircraft data to
  persist across sessions."
- **Retention is hours-scale, not days-scale** (`aircraft_track_expiry_hours`, default
  24h) — flights last hours, not the multi-day voyages `shipping_collector`'s
  `vessel_track_expiry_days` sizes for — pruned exclusively by `Housekeeper`, never
  inline by the collector (mirrors `prune_vessel_tracks`, not
  `FieldCollectorBase.prune_except_run`'s inline-prune pattern).
- **Client-side dead-reckoning interpolation is kept**, unchanged in substance from
  `0009` — `isFrozen`/`interpolatedPosition`/the bounded-freeze cap all still run in
  `flightradar.js`, just now driven by periodic REST poll responses instead of
  WebSocket pushes. The one thing that had to change: the client's own staleness
  bookkeeping is keyed off each aircraft's server-reported `last_seen` timestamp, not
  "was this hex present in the latest HTTP response" — the REST endpoint returns the
  full unfiltered fleet on every call (matching `ships/geojson`'s own shape), so
  presence in a response is no longer evidence of a fresh sighting the way a WS push
  used to be.

## Considered Options

- **Keep the region-keyed WebSocket-push architecture, just add a DB write-through** —
  rejected: still has no "resting" global picture between watched sessions (aircraft
  the collector never gets asked about still wouldn't be in the DB), so it doesn't
  actually solve the problem this ADR exists for; also keeps two parallel adsb.lol
  request paths (per-viewport live polling *and* whatever populates the DB) contending
  for the same undocumented rate limit, the exact complexity `0009` itself avoided by
  unifying tiers onto one polling mechanism.
- **A genuinely unbounded global sweep** (what `0009` already rejected) — still
  rejected, for the same reason: no confirmed adsb.lol capacity for it, and a
  self-hosted single-operator instance has nothing to gain from maximum global cadence
  everywhere over prioritizing where people are actually looking.
- **A static, precomputed air-traffic density model** (weighting the background sweep
  by known airport/airway corridors) — rejected for this iteration: no such reference
  data exists anywhere in this codebase yet, and the adaptive empty-cell
  deprioritization approximates the same effect (concentrate budget where traffic
  actually shows up) without new data to source and maintain. Revisit if the adaptive
  approach proves too slow to "learn" busy regions in practice.
- **One combined interest+scheduling channel inside `map_api`, no DB collector at
  all** — rejected: `map_api` and any future collector process are different processes
  by this codebase's convention (`AsyncCollectorBase`/`CollectorService` for
  data-acquisition, `map_api` for request-serving); Postgres is already the shared
  substrate everything else in this app uses to cross that boundary, so reusing it for
  the interest signal costs nothing new, while a bespoke IPC channel would.
- **Database-backed `AircraftCollector`, REST-served, viewer-interest DB-mediated as a
  side effect of the same read request** — chosen.

## Revisit if

adsb.lol's real-world rate limits (still undocumented/"dynamic") turn out not to
tolerate even this bounded, prioritized load — at that point the request budget or
starvation floor are the first things to relax, before reconsidering the architecture
itself (same posture `0009` took toward its own tunable cadence numbers). Also revisit
if the adaptive empty-cell deprioritization proves too slow to concentrate the
background sweep's budget on genuinely busy regions in practice — that's when a real,
static density reference dataset (rejected above for lack of existing data) would
become worth building. And revisit `aircraft`'s own unbounded row growth (no
master-row pruning exists, matching `ships`' identical, pre-existing limitation) if it
ever becomes a real problem for either the REST response size or the collector's own
read/write volume.
