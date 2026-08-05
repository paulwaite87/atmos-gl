# Accept Natural Earth's coastline precision for waves'/currents' land masking

> **Superseded (2026-08-05).** This ADR's own "Revisit if" clause fired: the user
> explicitly asked to address the coastline-crossing issue this ADR had deferred.
> `docs/adr/0013-switch-coastline-masking-to-gshhg.md` records the outcome -- GSHHG's
> 'h' (high) tier replaces Natural Earth for `coastline_land_mask()`/`LandMaskCache`
> everywhere they're used (waves, currents, sst, greenhouse_gases), fixing real
> single-cell misclassifications this ADR's investigation diagnosed (confirmed live:
> a Natural Earth gap at Tasmania's NW coast that GSHHG correctly classifies as land).
> The investigation and root-cause analysis below are kept for the record -- they're
> what informed the superseding decision's dataset choice, not stale/wrong. Also see
> that ADR for two OTHER, independently real and now-fixed mechanisms found during the
> same debugging session: a particle-engine respawn-fallback bug (unrelated to the
> coastline dataset), and the regrid-grid-resolution ceiling that neither dataset can
> fix on its own.

Found live during candidate #7 (particle-engine consolidation)'s waves checkpoint:
after fixing three real particle-engine bugs (a calm-cell unit mismatch, a coastal
one-frame advection overshoot, and a respawn land-avoidance retry budget too small for
land-dominated views — see this candidate's own commits), waves' animated bars still
visibly crossed onto land at specific, reproducible locations: Northland, New Zealand
(north of Auckland) and Tasmania, both consistently "waves entering from the west,
penetrating a short way inland, stopping before reaching the interior."

## Investigation

Traced the exact rendering pipeline (`WavesUpdater._masked_uv` → `LandMaskCache` →
`coastline_land_mask`, `lib/coastline.py`) directly against the real regrid grid at
Northland. The raw Natural Earth `"10m"` polygon (cartopy's finest available tier)
correctly classifies large, unambiguous points (Sahara, Amazon, Australia's interior,
deep ocean, even UK-regional checks: London/North Sea/English Channel) — but around
Northland's complex, convoluted coastline (a thin peninsula, many small islands/inlets,
the Bay of Islands is literally named for them), the regridded land mask shows a
genuinely noisy, "swiss cheese" pattern: isolated single-cell flips inconsistent with
the real, smooth coastline shape a few km away.

**Root cause**: Natural Earth's `"10m"` designation is a *cartographic scale*
(1:10,000,000), not 10-metre survey precision — it's a meaningfully generalized/
simplified coastline, coarser than what a real navigational or OSM-derived dataset
provides. This becomes visible specifically on complex, convoluted coastlines (thin
peninsulas, dense island groups) even though it's indistinguishable from a perfect
coastline on large, simple landmasses — which is why earlier verification (checking
Sahara/Amazon/UK) didn't catch it: those are exactly the areas Natural Earth handles
well.

Confirmed independently: `ui/modules/landmass.js` ("Landmass Outlines") draws
coastlines from MapTiler's own OpenMapTiles vector tiles (OSM-derived, effectively
arbitrary precision for a web map) — a completely different, much higher-fidelity
source than cartopy's bundled Natural Earth data. The user's own comparison ("if I
switch on Landmass Outlines, those accurately depict the coastlines") is what
confirmed this is a data-source gap, not a rendering bug: two independent coastline
sources exist in this app, at very different fidelities, and waves'/currents' masking
uses the coarser one.

**Asymmetry noted live**: the effect is visibly worse on the WEST coasts of both
Tasmania and Northland NZ than the east (east still shows a few, but noticeably less).
Most likely explanation (not independently confirmed, but consistent with known
oceanography): both sit in the Southern Ocean/Tasman Sea's prevailing-westerly swell
belt ("Roaring Forties"), so west-facing coasts see genuinely stronger, more frequent
swell activity than their sheltered, lee-side east coasts. Since bars need to actually
survive long enough (candidate #7's own lifetime fix) and drift far enough to reach a
coastline at all before any masking imprecision can be exposed, the calmer east coasts
simply have far fewer particles ever getting close enough to reveal the same underlying
mask imprecision -- the imprecision itself isn't necessarily asymmetric, the amount of
real wave activity testing against it is.

## Considered Options

- **Switch to a higher-fidelity coastline dataset for `LandMaskCache`** (e.g. GSHHG,
  or extracting/caching MapTiler's own OpenMapTiles geometry server-side) — would
  genuinely fix this. Rejected *for now*: a real, separate data-acquisition and
  integration effort (new dependency or tile-extraction pipeline, its own design
  conversation, likely its own performance trade-offs like the `10m`-vs-`50m` cost
  already measured for Natural Earth) — well outside candidate #7's scope (particle-
  engine consolidation), which was already the fourth compounding fix chased down this
  same rabbit hole live.
- **Accept the current Natural Earth precision as a known limitation** — chosen. The
  three real bugs this investigation *did* find and fix (calm-cell units, coastal
  look-ahead, land-avoidance retry budget) are genuine improvements landed regardless;
  the residual coastline imprecision on complex coastlines is a data-source ceiling,
  not something further shader/engine work can close.

## Revisit if

A future task specifically wants sharper coastline fidelity for ocean layers (waves,
currents) — at that point, evaluate GSHHG or a MapTiler-tile-derived geometry source
as its own scoped effort, with its own cost/precision trade-off analysis (mirroring how
the `"50m"`→`"10m"` Natural Earth change for waves was measured: ~21s one-time cost per
render-worker process for that smaller step alone).
