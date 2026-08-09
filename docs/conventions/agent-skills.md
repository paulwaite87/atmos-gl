# Agent Skills & PRD Workflow

Linked from [AGENTS.md](../../AGENTS.md).

## Issue tracker

Issues are tracked in this repo's GitHub Issues (paulwaite87/atmos-gl), via the `gh` CLI.
External PRs are not treated as a triage surface. See `docs/agents/issue-tracker.md`.

## Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`) — no repo-specific remapping. See `docs/agents/triage-labels.md`.

## Domain docs

Single-context layout — one `CONTEXT.md` + `docs/adr/` at the repo root. See
`docs/agents/domain.md`.

## Prioritized skills

The broader skill catalog under `~/.agents/skills/` is symlinked in globally, but these
are the ones actively in use for this repo:

- `tdd` — test-driven development; build features or fix bugs test-first
- `code-review` — review changes since a fixed point along Standards and Spec axes
- `wayfinder` — plan and track work too large for one session as a map of tickets
- `diagnosing-bugs` — diagnosis loop for hard bugs and performance regressions
- `to-issues` — break a plan/PRD into independently-gradable GitHub issues
- `to-spec` — turn a conversation into a PRD and publish it to GitHub (not `to-prd`,
  which is not an available skill in this repo despite the name's obviousness)
- `triage` — move incoming issues/PRs through the triage label state machine
- `implement` — implement a piece of work from a PRD or set of issues
- `improve-codebase-architecture` — scan for deepening opportunities, report, then grill through one
- `ubiquitous-language` — extract a domain glossary into `CONTEXT.md`
- `handoff` — compact the current conversation into a handoff doc for another agent
- `claude-handoff` — hand the current conversation to a fresh background agent
- `grilling` — grill the user relentlessly about a plan or design before building
- `grill-me` — a relentless interview to sharpen a plan or design
- `grill-with-docs` — same, informed by existing repo docs
- `domain-modeling` — build and sharpen the project's domain model, record ADRs
- `prototype` — build a throwaway prototype to answer a design question

**PRD workflow order:** before ever invoking `to-spec`, always run `grill-me` or
`grill-with-docs` first — or, at minimum, explicitly confirm with the user that
grilling isn't needed for this particular PRD. Never jump straight to `to-spec`.
