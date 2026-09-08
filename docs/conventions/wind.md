# Worked example: Particle overlay (wind)

Wind is the richest layer in the codebase — the only one that's simultaneously an
**Animated fill layer** and the source for **two different Particle overlay engines** (see
[layers.md](layers.md)). Waves and currents each drive one particle engine each; wind
drives both.

## Three mechanisms, one layer

`ui/modules/wind.js` sets up:

1. **The fill itself** — `createFillLayer` (`ui/modules/_webglfill.js`), exactly like
   temperature: an hour-animated GPU texture of wind speed/direction, backed by
   `WindUpdater.plot()` (`src/atmos_gl/tasks/wind.py`).
2. **Oriented-quad particles** — `_particles_gl.js`, the same engine `waves.js` uses. Each
   particle is a small oriented quad (an arrow/streak sprite) whose rotation tracks the
   local wind direction, advected across the GPU each frame.
3. **Streamline-ribbon particles** — `_streamparticles_gl.js`, the same engine `currents.js`
   uses. Particles trail a continuous ribbon along the flow rather than discrete oriented
   sprites — better suited to currents' and wind's longer, smoother flow lines.

Both particle engines read `ui/modules/_particlegl_primitives.js`'s shared GPU primitives
(buffer/texture setup, the advection step) — an earlier architecture-review extraction that
collapsed what used to be near-duplicated setup code between the two engines. See that
module's own docstring for the full oriented-quad vs. streamline-ribbon breakdown.

## Direction convention

Wind's direction field follows the same WMO "FROM" convention as waves (see CONTEXT.md's
"Direction convention (FROM)" entry) — the angle is where the flow arrives from, not where
it's heading. Getting this backwards silently points every particle 180° off; both particle
engines and the fill's own vector decode share this convention, so it only needs fixing
once (`lib/unpack.py`).

## Backend

`WindUpdater` (`src/atmos_gl/tasks/wind.py`) is its own bespoke `plot()` — not
`ScalarFieldUpdater` — since wind is a vector field (u/v components), not a single scalar.
It shares `Updater.regrid_for_lod` and the antimeridian seam-closing helper
(`close_lon_seam_for_contour`) with every other Animated fill layer, but its own encode
step writes both magnitude and direction into the data texture the particle engines sample.

## Where to look next

- [temperature.md](temperature.md) — the plain Animated fill layer, no particle overlay
- `ui/modules/_particlegl_primitives.js` — the shared GPU primitives both particle engines
  build on
- `ui/modules/waves.js` / `ui/modules/currents.js` — the single-engine instances of each
  particle mechanism, without wind's fill+both-engines combination
