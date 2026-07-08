# Don't extend _hoverpopup.js's interface to cover markers.js

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
