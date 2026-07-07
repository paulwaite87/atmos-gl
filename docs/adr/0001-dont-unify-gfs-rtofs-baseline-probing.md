# Don't unify the GFS/RTOFS baseline-probing loops

`resolve_gfs_baseline` (`lib/gfs.py`) and `resolve_rtofs_baseline` (`lib/rtofs.py`)
both loop over candidate (day, run) pairs and HEAD-probe until one answers, returning
the same baseline dict shape. A 2026 architecture review flagged this as a possible
dedup candidate ("Speculative" tier).

On inspection, a shared helper would save roughly 10-15 lines in exchange for one more
layer of indirection, with no bug or maintenance pain motivating it. GFS's 4-runs/day
candidate list, its `.idx` GRIB-sidecar probe target (vs. RTOFS's single daily "00"
cycle probing a whole NetCDF file directly), and its more complex
`resolve_gfs_baseline_with_coverage` sibling (which computes a dynamic
publish-window-aware "top hour" and has no RTOFS equivalent at all) are real,
load-bearing domain differences — not incidental copy-paste. Decided not to extract.

## Considered Options

- **Extract a shared `probe_candidate_runs(...)` helper** parameterized by the
  candidate-run list and a URL-builder callback — rejected: marginal line-count
  savings, no correctness motivation, adds indirection to two functions that are each
  already simple to read standalone.
- **Leave both resolvers independent** — chosen. They share a similar *shape*, not
  shared *code*; that's a normal, low-cost repetition rather than a maintenance
  hazard.

## Revisit if

A third baseline-resolving data source arrives sharing this shape, or the two loops
are ever found to have actually drifted apart in a way that caused a bug.
