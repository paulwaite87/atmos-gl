# Working Philosophy

Linked from [AGENTS.md](../../AGENTS.md). Governs *how* to approach any change in this repo,
not just what to build.

## Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## Surgical Changes

**Touch only what you must. Clean up only your own mess by default, but ask if other messes should be as well.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## Prefer Common Code

**When new work overlaps with something that already exists, look for a shared home
before writing a second copy.**

This is a standing preference, not a one-off — apply it by default on every task, not
just when asked. Before building a new class/module/layer that resembles an existing
one (a new field-vector layer's backend `Updater` next to `WindUpdater`, a new frontend
module next to an existing `ui/modules/*.js`), check whether the overlap is real and,
if so, extract or reuse rather than copy-paste. Call out the specific extraction
opportunity you found (what's shared, what's new) rather than silently duplicating or
silently refactoring — the user decides whether to take it in a given task's scope, but
the option should always be surfaced.

This preference has limits: only extract where the overlap is genuine, not superficial
— see "Deepening Template-Method Hierarchies" below for the template-method-specific
form of this same instinct. Genuine-vs-superficial is itself a judgment call, not a
fact to look up: when it's unclear which side of the line something falls on, put it
to the user rather than deciding it unilaterally — and treat whatever call they make
as correct for this codebase, not a decision to second-guess or quietly reverse later
without them. `docs/adr/0002-dont-extend-hoverpopup-for-markers.md` is one such call:
judged one way at the time (leave `markers.js` bespoke), then deliberately revisited
and judged differently later once unifying every popup became the explicit goal —
both were legitimate calls for the question being asked at the time, not a first
"wrong" attempt corrected by a second "right" one.

## Deepening Template-Method Hierarchies

**When a base class already owns control flow and lets subclasses override hooks,
extend those hooks — don't pull newly discovered duplication out as a narrow,
free-standing helper sitting alongside them.**

If a class hierarchy already uses the template-method pattern (a base class defining
the control flow, subclasses overriding hook methods — e.g. `FieldCollectorBase`'s
`resolve_baseline()`, `_expected_fhour_end()`, `backfill_hour()`), and you find
duplication in the surrounding control flow across subclasses, lift that control flow
into the base class too, exposing whatever varies as another override hook. Don't
extract just the innermost duplicated body as a standalone helper and leave two
near-identical control-flow shells sitting in the subclasses either side of it.

Two ~90%-identical loop/control-flow bodies in sibling subclasses are harder to audit
for drift than one control-flow body in the base class with each subclass's override
list visible at a glance — the override list *is* the domain-difference
documentation. A narrow helper extraction also tends to leave existing duplication
bugs in place rather than surfacing them: if a subclass already recomputes a value
independently in two places (once in an existing hook, once inline in the method
you're deepening), pulling that method's control flow into the base forces it through
the existing hook instead, fixing the duplication as a side effect.

This only applies where template-method structure already governs the modules in
question. Where no such structure exists — a handful of leaf modules just doing
similar-looking things — a narrow, local extraction (or no extraction at all) is the
right call; don't invent a base class/hook hierarchy from scratch to justify one.
`docs/adr/0002-dont-extend-hoverpopup-for-markers.md` is an example of this specific
point holding even after the ADR's own broader conclusion was later revisited: the
eventual fix widened `hoverPopup`'s parameters, not a class/hook hierarchy — no
base class was ever warranted, whichever way the wider unification call went.

## Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```
