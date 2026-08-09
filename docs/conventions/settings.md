# Settings Changes

Linked from [AGENTS.md](../../AGENTS.md).

`config/atmos-gl.json` is gitignored — it's the **live** config the running stack actually
reads and writes (via the config UI or hand-edits), and it drifts constantly during normal
use. `config/atmos-gl.json.tmpl` is the **tracked template**, committed to the repo, and is
the one to read when you need to know the current *shape* of the config
schema (section/option names, structure) for something like a code change.

When a task involves refactoring settings (adding/renaming/restructuring config
sections or options — e.g. the PWAT layer's config additions, or the `ozone`/`pwat`
critical-palette fields):

1. Modify and test the change against the live `config/atmos-gl.json` first — this is
   where iteration happens, exactly like any other manual settings change.
2. Once the shape has settled, update `config/atmos-gl.json.tmpl` to match it exactly.
3. Commit `config/atmos-gl.json.tmpl` alongside whatever other code changes are part of
   that refactor. Never commit `config/atmos-gl.json` itself (it's gitignored — don't
   force-add it).

A fresh checkout has no live `config/atmos-gl.json` at all — only the `.tmpl`. `make
up`/`make prod` bootstrap it automatically (see the Makefile's `bootstrap-config`
target: copies the template over if the live file is missing, never overwrites an
existing one). CI does the same as an explicit step before running pytest. Anything
that reads config outside those paths (a one-off script, a fresh test run) needs that
copy to exist first.
