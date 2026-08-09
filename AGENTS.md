# AGENTS.md — atmos-gl

A Docker Compose–based system that renders a live, interactive 3D globe (MapLibre GL JS
frontend, Python/FastAPI/PostGIS backend) showing real-time and forecast weather and world
events — GFS atmospheric/wave forecasts, RTOFS ocean currents, earthquakes, storms, volcanoes,
shipping, lightning, and more.

> This file (plus the topic files it links to under `docs/conventions/`) is the single source
> of truth for this repo's conventions. Claude Code also reads the root `CLAUDE.md`, which is
> now just a pointer here — don't duplicate instructions into `CLAUDE.md`, extend the docs
> below instead.

## Non-negotiable, every task

- **Never read, modify, or otherwise manipulate files matched by `.gitignore`.**
- **Never commit secrets** (API keys, credentials) to version control.
- Python package manager is **`uv`** exclusively — never `pip install`, always `uv add` / `uv run`.
  JS tests use plain `npm`.
- **`src/` is the only importable root.** The Dockerfile copies just `src/`; a Python file placed
  outside it (e.g. a root-level `collectors/`) is invisible inside the container. Details in
  [Architecture & repository layout](docs/conventions/architecture.md).
- Read [Working philosophy](docs/conventions/philosophy.md) before any non-trivial change — it
  governs how much to build, how surgical to be, and when to stop and ask instead of guessing.

## Commands

| Task | Command |
|---|---|
| Start dev stack | `make up` |
| Apply a code edit (restart, no rebuild) | `make reload` |
| Rebuild images (only for dependency/Dockerfile changes) | `make build` |
| Run Python tests | `make test` (optional: `make test include=<pattern>`) |
| Run JS/shader tests | `npm test` / `npm run test:shaders` |
| Lint (check only) | `make lint` |
| Lint + format + autofix | `make lint-fix` |
| Apply DB migrations | `make migrate` |
| Shell into the app container | `make bash` |
| Full command list | `make help` |

## Details, by topic

- [Working philosophy](docs/conventions/philosophy.md) — think-before-coding, simplicity first,
  surgical changes, prefer common code, deepening template-method hierarchies, goal-driven execution
- [Architecture & repository layout](docs/conventions/architecture.md) — collector class hierarchy,
  orchestration invariants, `src/` layout
- [Tooling](docs/conventions/tooling.md) — uv, Docker Compose, Makefile, CodeGraph
- [Testing & validation](docs/conventions/testing.md) — validating collector code without a running stack
- [Docker conventions](docs/conventions/docker.md)
- [Settings / config changes](docs/conventions/settings.md) — `config/atmos-gl.json` vs `.tmpl`
- [Git workflow](docs/conventions/git-workflow.md)
- [Agent skills & PRD workflow](docs/conventions/agent-skills.md) — issue tracker, triage labels,
  domain docs, skill catalog
