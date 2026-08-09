# CLAUDE.md — atmos-gl

Claude Code reads this file automatically, but the actual conventions and architectural
invariants for this repo live in **[AGENTS.md](AGENTS.md)** and its linked topic files under
`docs/conventions/`. That hierarchy is canonical — read it before making any changes. This file
is a pointer, not a second copy: don't add instructions here that belong in one of those files.

## Where to look

- **[AGENTS.md](AGENTS.md)** — start here: one-sentence project description, non-negotiables,
  command table, and the full topic index
- [Working philosophy](docs/conventions/philosophy.md) — think-before-coding, simplicity first,
  surgical changes, prefer common code, deepening template-method hierarchies, goal-driven execution
- [Architecture & repository layout](docs/conventions/architecture.md) — collector class hierarchy,
  orchestration invariants, `src/` layout
- [Tooling](docs/conventions/tooling.md) — uv, Docker Compose, Makefile, CodeGraph
- [Testing & validation](docs/conventions/testing.md) — validating collector code without a running stack
- [Docker conventions](docs/conventions/docker.md)
- [Settings / config changes](docs/conventions/settings.md) — `config/atmos-gl.json` vs `.tmpl`
- [Git workflow](docs/conventions/git-workflow.md)
- [Things to avoid](docs/conventions/things-to-avoid.md) — quick-reference checklist
- [Agent skills & PRD workflow](docs/conventions/agent-skills.md) — issue tracker, triage labels,
  domain docs, skill catalog

## Claude Code specifics not covered by AGENTS.md

`.claude/CLAUDE.md` (untracked — this repo's `.gitignore` excludes `.claude/` entirely) carries
CodeGraph MCP usage instructions and the local `PreToolUse` hook setup that only apply to Claude
Code sessions on this machine. Don't assume every contributor or session has it.
