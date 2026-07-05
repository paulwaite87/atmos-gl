# Domain context — worldmap-ng

Domain language for the map's data layers. Extend as terms are sharpened; keep
definitions to one sentence.

## Layers

| Term | Definition | Aliases to avoid |
| ---- | ---------- | ---------------- |
| **Scalar field** | A layer rendered as a single-scalar `contourf` heatmap over a value range — temperature, ozone, and stormwatch (CAPE) — sharing one renderer (`ScalarFieldUpdater`) and differing only by a `ScalarFieldSpec` (colormap, range, `extend`, key ticks, title). | scalar layer, heatmap layer |

A **Scalar field** is distinct from the vector layers (wind, currents), the
boundary/level layers (isobars, precipitation's `BoundaryNorm`), and SST's
runtime-computed range — those do not share the scalar-field renderer or its spec
shape.
