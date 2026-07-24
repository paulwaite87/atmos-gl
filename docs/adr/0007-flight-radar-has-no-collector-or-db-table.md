---
status: superseded by 0009
---

# Flight Radar has no collector, no DB table, no persistence at all

**Superseded by `docs/adr/0009-flight-radar-region-keyed-push-architecture.md`.**
The "no DB table" conclusion below still holds (aircraft state stays in memory, never
Postgres) — but "no collector, no persistence, no background process at all" turned
out wrong: per-session polling multiplies adsb.lol's load by the number of open
browser sessions, which this ADR's own "Revisit if" section anticipated. Kept here,
unmodified below, as the record of what was tried first and why it changed.

Every other event-feed layer in this app (quakes, storms, volcanoes, shipping,
lightning, fires, satellites) follows the same shape: a collector populates a DB
table, and the frontend reads a GeoJSON route backed by that table. Flight Radar
(aircraft positions from adsb.lol) deliberately breaks that pattern: there is no
collector, no `aircraft` table, no adapter, and no housekeeper involvement. The
entire feature is one stateless backend route that takes the frontend's current
map viewport, covers it with a small capped set of overlapping point+radius
circles (adsb.lol has no bounding-box or global-snapshot query, only point+radius),
queries adsb.lol live, dedupes by `hex` (the aircraft equivalent of MMSI) across
overlapping circles, and returns the result straight through. Nothing persists
between polls.

This was arrived at after explicitly working through the alternatives: a
background-collector-populated DB table alone gives a global-at-a-glance picture
but no smooth live tracking of aircraft actually moving on screen; a DB layer
running *alongside* a live viewport-driven path (the original plan) works but
costs two parallel data paths and a mechanism to stop them contending for
adsb.lol's request budget; pass-through-only costs the ability to show anything
without an open, live-polling viewport (there is no "resting" global picture) —
accepted, since the smooth-live-tracking goal is the actual point of this layer,
and covering the current viewport with circles works acceptably at any zoom level,
not just when zoomed in close.

## Considered Options

- **Collector + DB only** — rejected: no live/smooth tracking of aircraft moving,
  landing, taking off — the whole reason for wanting this layer.
- **Collector + DB (global, coarse) alongside a separate live pass-through (viewport,
  fresh) for when zoomed in** — rejected: two parallel data paths for one layer,
  plus a poll-as-lease mechanism to suspend the collector while the live path is
  active, to avoid doubling load against adsb.lol's undocumented "dynamic" rate
  limit. More moving parts than the value justified once the pass-through-only
  option was shown to work at every zoom level.
- **Live pass-through only, uniform at every zoom, no persistence** — chosen.

## Revisit if

adsb.lol's rate limits (currently undocumented/"dynamic") turn out to make
live, per-session polling impractical at scale, or a genuine need emerges for
aircraft data to persist across sessions (e.g. a historical-track feature) —
at that point the collector+DB shape this ADR rejected becomes worth
reconsidering, likely alongside the still-open API-key requirement adsb.lol
says is "planned for the future."
