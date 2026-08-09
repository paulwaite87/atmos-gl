# Tooling

Linked from [AGENTS.md](../../AGENTS.md).

- **Python**: 3.12
- **Package manager**: `uv` exclusively. Never suggest `pip install`; always `uv add`
  or `uv run`. Virtual environments are managed by `uv`.
- **IDE**: PyCharm. `.idea/` is excluded from git — never stage or commit it.
- **Docker Compose** drives all services. Changes to service structure belong in
  `docker-compose.yml`, not in ad-hoc `docker run` commands.
- **Makefile**: project-specific targets for common workflows (build, up, logs, test).
  See the command table in [AGENTS.md](../../AGENTS.md) or run `make help`.

## CodeGraph

If CodeGraph is installed (a `.codegraph/` index directory exists at the repo root and
the MCP server is available), use it before grep/find or reading files when you need to
understand or locate code — `codegraph_explore` (or `codegraph explore "<query>"` from
the shell) returns the relevant symbols' verbatim source plus call paths in one call.
It's optional tooling, not a project dependency — if it isn't installed, skip it
entirely and fall back to normal search/read.

Some sessions may also have local `PreToolUse` hooks in `.claude/settings.json`
enforcing this preference (e.g. blocking `Grep`, nudging away from delegating to a
file-reading subagent) — those are personal, untracked config, since this repo's
`.gitignore` excludes `.claude/` entirely. Don't assume every contributor or session has
them.
