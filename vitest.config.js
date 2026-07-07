import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Scope to ui/modules/*.test.js only. tests/gl-shaders/*.test.js are standalone
    // Playwright scripts (run via `npm run test:shaders`), not vitest suites -- vitest's
    // default glob would otherwise pick them up and fail with "no test suite found".
    include: ["ui/modules/**/*.test.js"],
  },
});
