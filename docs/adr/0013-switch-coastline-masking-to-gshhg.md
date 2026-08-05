# Switch coastline masking from Natural Earth to GSHHG

`docs/adr/0011-accept-natural-earth-coastline-precision-for-ocean-layers.md` documented
waves' animated bars visibly crossing land at Northland NZ and Tasmania, root-caused to
Natural Earth's `"10m"` designation being a *cartographic scale* (1:10,000,000), not
survey precision -- accurate on large simple landmasses, genuinely noisy ("swiss cheese"
misclassification) on complex, convoluted coastlines. That ADR deferred a real fix as
out of scope for candidate #7 (particle-engine consolidation), with a "Revisit if"
clause naming two candidate replacements. This ADR is that revisit.

**This debugging session found three independently real, separate mechanisms**, not
one -- worth recording distinctly since it's easy to attribute the whole symptom to
whichever was investigated first:

1. **A particle-engine respawn-fallback bug** (unrelated to the coastline dataset).
   `UPDATE_FS`'s respawn logic (`ui/modules/_streamparticles_gl.js`) retried up to 32
   times to find a valid (ocean) respawn position, but on exhausting all 32 attempts
   fell back to the LAST candidate -- entirely unvalidated. Measured live (extending
   `streamparticles_respawn_land_avoidance.test.js` to more extreme land fractions):
   at 96.9% land coverage (a coastline-hugging view), 35.9% of respawns landed on
   land, averaging 47.5% of the way across the visible land area -- not "slightly on
   land," scattered arbitrarily deep into it. Fixed by holding the particle at its own
   current position on exhaustion instead, retrying next frame rather than gambling
   once (see the shader's own comment at the fix site for the full mechanism).
2. **Genuine Natural Earth coastline gaps.** Confirmed live at Tasmania's NW coast
   (Cape Grim area): the exact production grid cell at 145.12°E, -40.80°S is classified
   "ocean" by Natural Earth but "land" by GSHHG -- a real single-cell misclassification
   of the kind ADR-0011's own investigation described. GSHHG's 'h' tier fixes this
   specific case. (An earlier check of this same investigation mistakenly tested an
   adjacent land cell and concluded GSHHG "agreed" with Natural Earth here -- it does
   not; that was a coordinate error, corrected before this ADR was written.)
3. **The regrid-grid-resolution ceiling**, which neither dataset can fix alone. At one
   other Tasmania location, GSHHG and Natural Earth were found to agree (both say
   "ocean") at a cell that, on the sharp `landmass.js` vector-tile coastline, reads as
   land -- consistent with a genuinely tiny real coastal feature (a small bay/inlet)
   that both datasets correctly capture, but which the render's `_WAVES_REGRID_STEP_DEG`
   grid (~8.9km/cell) is too coarse to represent precisely: the whole 8.9km cell gets
   marked "valid ocean" even though only a sliver of its true area is water. This is a
   separate, real limitation this ADR does NOT fix -- see "Revisit if" below.

This ADR covers mechanism 2 (the dataset swap); mechanism 1 was fixed directly in
`_streamparticles_gl.js` in the same session; mechanism 3 is recorded but deliberately
not addressed here.

## Dataset choice: GSHHG vs. extracting MapTiler's own vector tiles

`landmass.js` already draws coastlines from MapTiler's OpenMapTiles vector tiles
client-side, at a fidelity used as the very comparison point that first confirmed
Natural Earth was part of the problem. Extracting that same source server-side was the
other option ADR-0011 named.

Checked before choosing: cartopy's `NaturalEarthFeature` is a thin wrapper over
`cartopy.io.shapereader.Reader`, which loads *any* shapefile generically -- GSHHG ships
as shapefiles too, so it drops into the exact same `unary_union` + `shapely.contains_xy`
shape `coastline_land_mask()` already had, no new Python dependency. Reusing MapTiler's
vector tiles server-side, by contrast, would need a new MVT/protobuf-parsing dependency,
`MAPTILER_API_KEY` added to `layer_builder`'s environment (currently only `map_api` has
it), a tile-fetch-and-stitch pipeline that doesn't exist anywhere in this codebase, and
a check on whether MapTiler's terms even permit that volume of automated server-side use
(the existing key is scoped for interactive client tile-fetching, spread across many
users' viewports). GSHHG chosen: substantially lower integration risk on every axis
checked, and it's the standard dataset for exactly this class of problem (used by NOAA
and other GIS/oceanography tooling).

## Tier: 'h' (high, ~1:1,000,000)

GSHHG ships five tiers (full/high/intermediate/low/crude). `l` (low, ~1:12,000,000) is
roughly Natural Earth's own coarseness and would just reproduce ADR-0011's problem;
`h` is meaningfully finer than the failure case without `f` (full)'s much larger
file/load cost.

## Real costs found live (not assumed)

Measured directly against the real `GSHHS_h_L1` shapefile (144,749 polygon features)
inside a `layer_builder` worker, rather than estimated:

- **Load + union: ~227s** (load 70s, `shapely.make_valid` repair pass 7s, `unary_union`
  150s) -- over 10x Natural Earth's own ~21s equivalent (ADR-0011's own measured figure
  for its 50m->10m step).
- **A real blocking bug, not just a perf concern**: `unary_union()` on the raw
  geometries raised `GEOSException: TopologyException: side location conflict` -- 1 of
  144,749 polygons is invalid by shapely's strict standards. Natural Earth's much
  smaller, cleaner feature set never hit this. Fixed with a `shapely.make_valid()` pass
  over every geometry before `unary_union` -- confirmed working and correct against
  known points (Sahara=land, mid-Pacific=water, Auckland CBD=land).
- **The canonical download mirror is impractically slow from this deployment**:
  soest.hawaii.edu (GSHHG's own distribution point) throttled to ~5-10KB/s when
  measured directly (a tiny 15KB file took 2.8s; the full 142MB bundle projected to
  4+ hours) -- not a large-file-specific issue, a uniform per-connection cap. The
  Generic Mapping Tools project's GitHub Releases mirror (`GenericMappingTools/
  gshhg-gmt`, identical file, same version 2.3.7) served the same 142MB in **24s**
  (~6MB/s) from GitHub's own CDN -- used instead.

## Caching: disk-persist the final unioned geometry, not just the downloaded shapefile

The pre-existing `_COAST_GEOM_CACHE` (module-level, in-memory only) meant every fresh
or crash-recycled render worker would re-pay Natural Earth's ~21s load cost -- acceptable
at that price. At GSHHG's real ~227s, and with the "everywhere" scope decision below
meaning every land-masking caller (waves, currents, sst, greenhouse_gases) now blocks on
the same warm-up, that stopped being acceptable. `_load_gshhg_land_union()` additionally
serializes the final unioned geometry to disk (WKB) after the first successful build:
only the very first worker ever to need it pays the ~227s cost; every worker after that
(including ones spawned after this container's own lifetime, as long as the cache dir
persists) just deserializes the cached geometry.

Acquisition otherwise mirrors the existing Natural Earth precedent exactly: cartopy's
own NE downloads already cache to a container-local path
(`~/.local/share/cartopy/shapefiles/...`) that isn't bind-mounted -- ephemeral across
container recreation, but shared and persistent across every worker process within one
container's lifetime (workers share the container filesystem). GSHHG's shapefile
download and its derived union cache both land in an equivalent `~/.local/share/gshhg/`
path, same characteristics, same graceful `None`-on-failure fallback contract
`coastline_land_mask()` already had for a Natural-Earth network failure.

## Scope: every `coastline_land_mask()`/`LandMaskCache` caller, including sst.py's land tint

`coastline_land_mask()` is called both via `LandMaskCache` (waves, currents -- where
ADR-0011's bug was actually found and reported) and directly by `sst.py`/
`greenhouse_gases.py` (smooth heatmap textures, where the same misclassification would
be far less perceptually visible). Swapped **everywhere**, not scoped to just the two
reported layers -- deliberately, since `_COAST_GEOM_CACHE` is keyed by bbox, and every
caller already shares the same global bbox: moving everyone onto one shared GSHHG tier
means only ONE geometry is ever built per worker process, reused by every caller, which
is cheaper in aggregate than the previous split-tier setup (Natural Earth's `"50m"` for
sst/greenhouse_gases and `"10m"` for waves/currents were two separately-cached
geometries).

`sst.py`/`greenhouse_gases.py` also each had a *second*, separate Natural Earth usage --
`cfeature.NaturalEarthFeature(...)` passed directly to matplotlib's `ax.add_feature()`
for their land-tint background overlay, not going through `coastline_land_mask()` at
all. Moved to GSHHG too, via a new `gshhg_land_feature()` helper (wraps the same unioned
geometry in a `cartopy.feature.ShapelyFeature`), for full visual consistency between the
tint and the mask it sits under.

## Considered Options

- **GSHHG 'h', scoped to LandMaskCache only (waves/currents)** -- considered, not
  chosen: the shared-cache economics above make the full swap cheaper in aggregate, not
  more expensive, so there was no real cost/benefit case for the narrower scope.
- **GSHHG 'h', in-memory cache only (no disk persistence)** -- rejected once the real
  ~227s cost was measured: every fresh/recycled worker would pay it, a real render-
  latency cost on worker startup across every land-masking layer.
- **Declare the whole symptom "just a regrid-resolution ceiling" and do nothing** --
  rejected: mechanism 2 above (the Cape Grim gap) is a genuine, fixable dataset
  inaccuracy, confirmed live with a corrected point check, independent of the
  regrid-resolution mechanism found at a different location.
- **GSHHG 'h', disk-cache the final union, applied everywhere including the land-tint
  overlays** -- chosen.

## Revisit if

A future render workload needs genuinely crisp coastline edges at high zoom, or
accurate masking of coastal features narrower than `_WAVES_REGRID_STEP_DEG`/
`_CURRENTS_REGRID_STEP_DEG` (~8.9km) -- that needs a regrid-resolution increase (a
real, separate, costlier change: larger textures, more render cost per hour), not a
coastline-dataset change. Neither Natural Earth nor GSHHG can fix mechanism 3 above on
their own.
