# `performance_tier` lives in `common`, not `layer_builder`

Issue #200 (Global performance tier) landed narrowed to a single lever for v1:
`LayerBuilder`'s render-concurrency cap (`_max_workers`). Every other candidate lever —
a `data_collector.runs_per_day` cadence ceiling, a global `level_of_detail` render
ceiling — was cut from scope during grilling, leaving `LayerBuilder` as the only thing
this setting actually drives right now.

Given that, the tightly-scoped, honest home for the setting would be
`layer_builder.performance_tier`, next to the existing `layer_builder.enabled`. We
chose `common.performance_tier` instead: this is meant to be a general "how much
CPU/RAM is this host willing to spend" switch, not a `layer_builder`-specific one — v1
only wires it into `LayerBuilder` because that's the one lever grilling confirmed
addresses the actual OOM incident, not because the concept is inherently scoped there.
Future interventions (a collector cadence ceiling, a render-detail ceiling, whatever
else) are expected to read the same `common.performance_tier` value rather than each
inventing their own tier setting.

Config UI tab placement is independent of section name (each tab template lists which
sections populate it — see `templates/config.html`'s `render_tab_group()` calls), so
this choice costs nothing UI-wise; `performance_tier` shows on the Global tab either way.

## Considered Options

- **`layer_builder.performance_tier`** — rejected: tightly scoped and honest about
  today's actual behavior, but would need a config migration (or a second key) the
  moment a second subsystem wants to read the same tier, and starts to look like
  `layer_builder.enabled`-style scope creep the first time that happens.
- **`common.performance_tier`** — chosen: costs a moment of "why does `LayerBuilder`
  read a `common` setting" confusion today, in exchange for not needing to move/migrate
  the key when the next intervention (cadence, render detail, ...) wants to read it too.

## Revisit if

v1 ships and no second intervention ever materializes — at that point the forward-looking
justification for keeping it in `common` no longer holds, and moving it to
`layer_builder.performance_tier` (with a small migration) becomes the honest move.
