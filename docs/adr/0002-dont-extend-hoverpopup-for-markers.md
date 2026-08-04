# Don't extend _hoverpopup.js's interface to cover markers.js

> **Superseded (2026-08-02).** A later architecture review (candidate #6, originally
> just "unify popupCard's callsign-blue title styling") widened in scope to "make
> popup functionality a one-stop-shop for every instance" -- an explicit product
> decision to prioritize one consistent mechanic over minimizing per-adopter
> interface surface, not a second caller organically showing up needing the same
> four axes. `hoverPopup` now accepts `layerId` as a string-or-array, a configurable
> `event` ("enter"/"move"), and a live `enabled` predicate; `markers.js` migrated onto
> it, gaining the same sticky-hover/close-delay-grace-period behaviour every other
> adopter already had (a deliberate, accepted behaviour change, not a regression).
> The reasoning below is kept for the record -- it was correct for the question it
> answered ("does a second caller justify this seam"), which is a different question
> than "do we want one mechanic regardless."

`ui/modules/_hoverpopup.js`'s `hoverPopup(map, layerId, {offset, html})` owns the shared
Popup-construction + mouseenter/mouseleave + cursor + teardown mechanic behind
`quakes.js`, `storms.js`, `volcanoes.js`, and `satellites.js`. A 2026 architecture review
flagged `markers.js`'s weather-popup wiring as a similar-looking dedup candidate
("Worth exploring" tier).

On inspection, `markers.js` differs from `_hoverpopup`'s four adopters on four axes at
once, not one:

- **Event model** — binds `mousemove`/`mouseleave`, not `mouseenter`/`mouseleave`;
  deliberate, so the popup tracks continuously and doesn't flicker when the cursor
  crosses between the adjacent dot layer and label layer for the same place.
- **Multi-layer** — binds across `[dotLayerId, labelLayerId]`, not a single `layerId`.
- **Live enable/disable** — `weatherEnabled` is checked inside the handler and flips via
  `refresh()` with no remount/rebind, unlike `_hoverpopup`'s callers, which bind/unbind
  for the whole layer lifetime.
- **`maxWidth: '240px'`** on the `Popup` — an option `_hoverpopup` doesn't expose (only
  `offset`).

Growing `_hoverpopup`'s interface to cover all four would add parameters only
`markers.js` would ever set (event name, an array of layer ids, a live-enabled
predicate, maxWidth) — a hypothetical seam, not a real one; no other caller exercises
any of them. Decided not to extend.

## Considered Options

- **Extend `hoverPopup(map, layerId, {...})` to accept an array of layer ids, a
  configurable event pair, an enabled predicate, and `maxWidth`** — rejected: the
  interface would grow to nearly match `markers.js`'s own current implementation size,
  making the module shallow relative to its one new adopter.
- **Leave `markers.js`'s popup wiring bespoke** — chosen. It shares the same general
  idea (a hover popup) as the four adopters, not the same concrete shape; that's normal,
  low-cost divergence, not duplication.

## Revisit if

A second caller shows up needing multi-layer binding, `mousemove` tracking, or a live
enable/disable gate — at that point two adapters justify widening the seam. Until then,
`_hoverpopup`'s interface stays sized to its four existing, uniform adopters.
