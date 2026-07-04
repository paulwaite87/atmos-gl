# Ubiquitous Language

## Skill system

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Skill** | A directory containing a `SKILL.md` file that gives Claude Code a specialized, invocable capability | Command, tool |
| **SKILL.md** | The file inside a Skill directory defining its name, description, and process | — |
| **Skill catalog** | The full set of Skill directories that exist on disk, canonically under `~/.agents/skills/` | Skills folder |
| **Global skill** | A Skill discoverable from `~/.claude/skills/` (typically symlinked from the Skill catalog), usable in any repo on the machine | User-level skill |
| **Project skill** | A Skill scoped to `<repo>/.claude/skills/`, usable only in that repo | Repo-level skill |
| **Plugin skill** | A Skill installed via a marketplace, e.g. under `~/.claude/plugins/marketplaces/...` | — |
| **Available skills** | The subset of the Skill catalog listed as invocable to Claude in a given session; fixed at session start | Active skills, loaded skills |
| **Prioritized skill** | A Skill explicitly documented in a repo's `CLAUDE.md` as actively relevant to that repo's workflow | — |

## Repo skill configuration

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Issue tracker** | The system where a repo's issues and PRDs live, read and written by Skills like `to-issues`, `triage`, `to-prd` | — |
| **Triage role** | One of five canonical states a Skill like `triage` moves an issue through: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix | — |
| **Triage label** | The actual label string in a repo's issue tracker that a Triage role maps to | — |
| **Domain docs** | A repo's `CONTEXT.md` (domain glossary) plus `docs/adr/` (architectural decisions), consumed by Skills like `improve-codebase-architecture` and `tdd` | — |
| **Single-context layout** | A domain docs layout with one `CONTEXT.md` and one `docs/adr/` at the repo root | — |
| **Multi-context layout** | A domain docs layout with a root `CONTEXT-MAP.md` pointing to per-context `CONTEXT.md` files, typically in a monorepo | — |

## Git / PR workflow (agent-assisted)

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Feature branch** | A branch named `feature/<short-description>` or `fix/<short-description>`, branched from `master`, holding one unit of work destined for a PR | — |
| **Worktree** | A separate checkout of the same repo (e.g. under `.claude/worktrees/`) on its own branch, used to isolate a prior or parallel agent session's changes | — |
| **Orphan container** | A running or stopped Docker container left over from a service that no longer exists in the current `docker-compose.yml` | — |

## Relationships

- A **Skill** belongs to the **Skill catalog**; from there it may additionally be a **Global skill**, a **Project skill**, or a **Plugin skill** depending on how it's discovered.
- **Available skills** is a session-scoped subset of the **Skill catalog** — being on disk does not guarantee membership.
- A **Prioritized skill** is always drawn from the **Skill catalog** and is documented per-repo in `CLAUDE.md`, independent of whether it's currently in **Available skills**.
- Each **Triage role** maps to exactly one **Triage label** string per repo.
- A **Feature branch** is created from `master`, produces a PR, and is deleted (along with its remote counterpart) once merged.
- A **Worktree** carries its own branch, independent of the main checkout's current branch; it can go stale (fall behind `master`) without anyone noticing until it's inspected.

## Example dialogue

> **Dev:** "I added a symlink for `wizard` under `~/.claude/skills/` — why can't I invoke `/wizard` yet?"
> **Domain expert:** "Being in the **Skill catalog** isn't enough — it also has to be in this session's **Available skills**, and that list is fixed at session start. Either type `/wizard` explicitly, which works even when it's not listed, or start a fresh session."
> **Dev:** "And if I want `wizard` to always show up as relevant to this repo specifically?"
> **Domain expert:** "That's what makes it a **Prioritized skill** — you document it in `CLAUDE.md`'s Agent skills section. That's a documentation fact, not a technical one — it doesn't change what's actually loaded."
> **Dev:** "Got it. Separately — is `feature/data-status-dashboard` still around after merging its PR?"
> **Domain expert:** "No, we deleted the **Feature branch** locally and on the remote right after the merge. But there was also a stale **Worktree** sitting at an old commit under `.claude/worktrees/` — that wasn't tied to any PR, just an isolated checkout from a prior session that never got cleaned up."

## Flagged ambiguities

- "install" was used loosely to mean "make available," but no literal install step exists for a Skill that's already symlinked from the **Skill catalog** into `~/.claude/skills/`. The real gap is between the **Skill catalog** (disk) and **Available skills** (session state) — recommend reserving "install" for the actual catalog → `~/.claude/skills/` symlink step, and using "prioritize" for the `CLAUDE.md` documentation action.
- "skill" alone was sometimes used to mean the directory, sometimes the `SKILL.md` file, and sometimes the invocable capability itself. These point at the same thing, but worth being precise about when diagnosing why a skill isn't showing up.
