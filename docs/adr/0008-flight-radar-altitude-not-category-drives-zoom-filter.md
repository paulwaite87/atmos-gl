# Flight Radar's zoom-density filter uses altitude, not ADS-B category

Shipping's zoom-based density filter uses `length` (a continuous, always-present
physical attribute) for the MapLibre `step` zoom filter, and a *separate*
discrete attribute (`vessel_type`) purely for icon color/selection. Flight Radar
mirrors that split, but the two candidate fields for the "continuous, reliable
size-like scale" role have different real-world reliability: adsb.lol's `category`
field (A1-A7 light→heavy/high-performance, B0-B7 rotorcraft/glider/balloon/drone,
C0-C7 ground vehicles/obstacles) looks like an obvious size analogue to `length`,
but in practice a large share of real-world transponders omit it or report
`A0`/`B0`/`C0` ("no info") — using it as the primary zoom-density signal risks most
aircraft collapsing into one fallback bucket. `alt_baro` (barometric altitude) is a
continuous number that's almost always present whenever an aircraft is airborne,
and correlates reasonably well with "how interesting is this at low zoom" the same
way ship length does (a long-haul cruise-altitude jet is more globally significant
than a light aircraft doing local circuits at 2,000ft).

Decision: **altitude drives the zoom-density filter; `category` is used only for
icon selection**, where its gaps just mean "generic aircraft icon" rather than
breaking the density filter. `C*` categories (ground vehicles/obstacles) are
filtered out entirely — not aircraft in flight, don't belong in a flight-tracking
layer. `B*` categories (gliders/balloons/drones) get their own visual treatment
rather than being folded into the size scale.

## Considered Options

- **`category` drives the zoom filter** (the obvious analogue to `vessel_type`) —
  rejected: too often missing/unreliable in real ADS-B data to be the primary
  density signal.
- **Altitude drives the zoom filter, `category` drives icon selection only** —
  chosen.

## Revisit if

adsb.lol's `category` field turns out to be populated far more reliably in
practice than assumed here (this was a documentation-derived risk, not
empirically measured against real traffic yet) — worth re-checking once the
layer is live against real data.
