// Extracts shader source constants (and any other named consts/functions) from the
// REAL ui/modules/_particles_gl.js file via a sandboxed vm eval -- not a
// reimplementation. This lets shader-level tests exercise the exact GLSL that ships to
// production, not a JS/Python re-derivation of what the shader is supposed to do
// (which could silently drift from the real thing).
//
// Only strips the ES module import lines (none of which have side effects needed at
// module-evaluation time for these consts) and `export` keywords, then evaluates the
// rest verbatim.
import fs from "node:fs";
import vm from "node:vm";

export function extractFromParticlesEngine(path, names) {
  let src = fs.readFileSync(path, "utf8");
  src = src.replace(/^import .*$/gm, "");
  src = src.replace(/^export function/gm, "function");
  src = src.replace(/^export const/gm, "const");

  const context = { window: { MAP_UI: "" }, console };
  vm.createContext(context);
  const exportStmt = `globalThis.__extracted = { ${names.join(", ")} };`;
  vm.runInContext(src + "\n" + exportStmt, context, { filename: path });
  return context.__extracted;
}

/**
 * Calls a layer module's loadLayer(map, config) with every one of its own imports
 * stubbed out, capturing the opts object it actually passes to
 * createParticleGLController -- so a test can assert on the REAL production wiring
 * (e.g. "does waves.js really set landReset"), not a hand-copied literal that could
 * silently drift from what the file actually does.
 *
 * Safe to call generically: none of a layer module's top-level closures inside
 * loadLayer touch `map`/MapLibre until createParticleGLController/liveLayerSync are
 * invoked, and both are stubbed here, so the rest of loadLayer's body never runs.
 */
export function captureParticleControllerOpts(path, exportedFn = "loadLayer") {
  let src = fs.readFileSync(path, "utf8");
  src = src.replace(/^import\s*\{[^}]*\}\s*from\s*['"][^'"]+['"];?$/gm, "");
  src = src.replace(/^export function/gm, "function");
  src = src.replace(/^export const/gm, "const");

  const captured = { opts: null };
  const context = {
    window: { MAP_UI: "", WM_API: "" },
    document: { getElementById: () => null },
    console,
    createParticleGLController: (_map, opts) => {
      captured.opts = opts;
      return { mount() {}, refresh() {}, unmount() {} };
    },
    createParticleGLLayer: (_map, opts) => {
      captured.opts = opts;
      return () => {};
    },
    // waves.js (and others) also call createFillLayer for their heat fill, alongside
    // the particle controller this helper actually cares about -- stub it so
    // evaluating the module doesn't throw ReferenceError.
    createFillLayer: () => () => {},
    // Real opacityUniform behaviour isn't needed here (this helper only cares about
    // the particle-controller opts object) -- a passthrough of the fallback is enough
    // to let modules that call it (waves.js, wind.js, ...) evaluate without throwing.
    opacityUniform: (_cfg, fallback) => fallback,
    liveLayerSync: () => () => {},
  };
  vm.createContext(context);
  vm.runInContext(src, context, { filename: path });
  context[exportedFn]({}, {});
  return captured.opts;
}
