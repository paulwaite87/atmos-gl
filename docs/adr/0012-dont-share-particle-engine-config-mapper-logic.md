# Don't share config-mapper logic across the particle engine's consumers

`wind.js`, `waves.js`, `currents.js`, and `jetstream.js` each pass their own
`speedFromConfig`/`hFromConfig`/`thicknessFromConfig`/etc. mapper functions to
`createCurrentParticleGLLayer` (`_streamparticles_gl.js`). A 2026 architecture review
(the same one behind candidates A/B, architecture review candidate "particle engine
opts-coverage") flagged a shared "clamp-scale-fallback" helper across these as a
possible dedup candidate ("Speculative" tier, candidate C).

This was actually the SECOND time this exact question was raised. `jetstream.js`'s
own `speedFromConfig` already carries an in-code comment declining it at 2 data
points (wind linear, currents quadratic — "the shape isn't actually consistent
enough... to be worth lifting into a shared helper"). Re-examined here at 4 data
points (adding waves and jetstream itself), the answer is the same, with more
evidence, not less:

- **The "clamp" isn't one behavior.** Two genuinely different semantics are mixed
  under that single word: *hard-fallback-on-range-violation* (wind's
  `speedFromConfig`: `ui >= 10 && ui <= 100 ? ui : 50` — an out-of-range value is
  entirely replaced by the fallback) vs. *soft-clamp-with-NaN-fallback* (waves'/
  currents': `Math.min(100, Math.max(0, p))` — an out-of-range-but-finite value is
  clamped to the nearest bound instead). These produce different outputs for the
  same out-of-range input; a shared helper would have to take a parameter selecting
  which semantic applies per call, which mostly just re-adds the per-caller
  branching the helper was supposed to remove.
- **The scale shape itself varies.** wind's and jetstream's `speedFromConfig` are
  linear; currents' is quadratic (`(v/100)^2 * 3.2`); waves' is linear but divides
  by 1000, not 100 or 500 — three distinct scales across four consumers.
- **One genuine exact-shape duplicate exists, and it's too small to be worth
  extracting.** `currents.hFromConfig` and `jetstream.hFromConfig` share the same
  clamp/fallback/range-map structure, differing only in two output-range constants.
  `wind.hFromConfig` looks similar at a glance but isn't — different clamp bounds
  (`[10,100]` vs `[0,100]`) and a `frac` that divides by 500, not 100 (a
  deliberately compressed sub-range, per its own comment). Pulling the
  currents/jetstream pair into a shared `linearRangeMap(...)` helper would trade two
  ~3-line functions differing only in two numbers for an indirection to a shared
  helper — not worth it at this size.

## Considered Options

- **Extract one general clamp-scale-fallback helper** for all four consumers'
  mapper functions — rejected: the semantics genuinely diverge (hard-fallback vs.
  soft-clamp, linear vs. quadratic vs. divide-by-1000), so a shared helper would
  need to reintroduce per-caller parameterization for the exact behavior that makes
  each consumer's tuning what it is.
- **Extract a narrow helper just for `currents.hFromConfig`/`jetstream.hFromConfig`**
  (the one real exact-shape match) — rejected: two ~3-line functions differing only
  in two numeric constants don't carry the weight of an extraction. Each stays
  independently readable/tunable without an indirection to a shared helper for what
  amounts to two numbers.
- **Leave every mapper function independent** — chosen. Each consumer's mapper was
  arrived at through live tuning against its own field/UI ranges (see each
  function's own comment in `wind.js`/`waves.js`/`currents.js`/`jetstream.js`); the
  resemblance between them is surface-level, not shared code.

## Revisit if

A fifth particle-engine consumer arrives whose mapper functions are *structurally*
identical to an existing one (not just similarly-shaped), or `currents.hFromConfig`/
`jetstream.hFromConfig` grow enough additional shared structure beyond the two
output-range constants that duplicating it a third time would be a real
maintenance hazard, not just two numbers.
