# Keep waves.js on the oriented-quad particle engine

`_particles_gl.js` and `_currentparticles_gl.js`'s docstrings both still asserted, as of
this writing, that wind and waves share the oriented-quad engine while currents is a
permanently isolated streamline engine. That's stale: since PR #133/#134, `wind.js`
actually calls `createCurrentParticleGLLayer` (currently framed as a PROTOTYPE, not yet
committed to permanently). `waves.js` is now the sole remaining consumer of
`_particles_gl.js`, which a 2026 architecture review flagged as the now-live question the
stale docstrings were papering over: should waves move to the currents engine too, or
does it have its own load-bearing domain difference?

It has one. `waves.js` renders short **bars perpendicular to the wave direction** — a
fixed-length tick marking swell-crest orientation at a point, not a trail. The currents
engine's whole design is a **streamline traced upstream through the velocity field**: the
tail literally shows where the water just came from (see `_currentparticles_gl.js`'s own
docstring on why streamline replaced the old stored-history ring). A perpendicular crest
tick has no "where did this come from" to trace — there's nothing for a streamline
integration to compute. This isn't the same kind of distinctness that motivated wind's
migration (wind's oriented-quad STREAK was already a directional along-flow indicator,
just a cruder one than a proper streamline); waves' BAR primitive is a different concept
entirely.

## Considered Options

- **Migrate waves.js onto `_currentparticles_gl.js`** — rejected: there's no streamline
  interpretation of a perpendicular crest tick to migrate to. Forcing it through the
  streamline engine would mean inventing a new primitive mode there anyway, which is the
  same cost as waves.js keeping its own, with none of the benefit.
- **Leave waves.js on `_particles_gl.js`** — chosen. Correct the two docstrings to stop
  asserting a split that no longer matches wind.js's actual wiring, and record that
  waves' continued use of the oriented-quad engine is a deliberate reading of its BAR
  primitive, not leftover staleness.

## Revisit if

`_particles_gl.js`'s BAR primitive mode is ever changed to something a streamline could
represent, or wind's PROTOTYPE migration is reverted back onto `_particles_gl.js` (at
which point `_particles_gl.js`'s "Consumers: waves.js" note needs correcting again).
