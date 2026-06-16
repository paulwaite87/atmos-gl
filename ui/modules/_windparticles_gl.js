/**
 * Backwards-compatibility shim.
 *
 * The wind particle engine has been generalised into the shared streak-particle engine
 * (_streakparticles_gl.js), which now serves wind, currents, and any future u/v-field
 * flow layers. This module re-exports that engine under the original wind-named symbol
 * so existing importers (wind.js) keep working unchanged. Wind behaviour is identical:
 * it's the same code, with sectionKey defaulting to 'wind' here.
 */
import { createStreakParticleGLLayer } from './_streakparticles_gl.js';

export function createWindParticleGLLayer(map, opts = {}) {
    return createStreakParticleGLLayer(map, { sectionKey: 'wind', ...opts });
}
