#!/usr/bin/env python3
"""Regression test for issue #283: contourf occasionally raised
`ValueError: Sequences of multi-polygons are not valid arguments` for specific,
deterministic field topologies (observed live: "wind: static render f006 failed").

Root cause (confirmed by reading cartopy's installed source): cartopy's
`Projection._rings_to_multi_polygon()` -- the internal geometry step contourf's
`get_datalim()` call uses while reprojecting an antimeridian-wrapping contour ring
-- had a "leftover interior rings" branch that could compute
`boundary_poly.intersection(polygon)` and get back a `shapely.MultiPolygon` (when
the leftover ring splits the map boundary into two disconnected pieces), then
append that MultiPolygon straight into `polygon_bits` alongside plain
`(coords, holes)` tuples. The final `sgeom.MultiPolygon(polygon_bits)` call then
raises, because shapely's MultiPolygon constructor explicitly refuses a sequence
containing another MultiPolygon ("no implicit flattening").

cartopy 0.25.0 fixed this by flattening: `if isinstance(polygon, sgeom.
MultiPolygon): polygon_bits.extend(polygon.geoms)`. We were previously pinned to
cartopy==0.24.1 (PR #239) to dodge an unrelated pcolormesh GEOSException bug
(#238) -- but nothing in this codebase calls pcolormesh anymore (air_quality.py
moved to imshow; sst.py/greenhouse_gases.py moved off the matplotlib Plot
pipeline entirely), so that tradeoff no longer applies and cartopy was bumped
back to 0.25.0.

This test reproduces the underlying geometry bug directly -- no GFS data or
contourf call needed, so it isn't flaky the way triggering it via real weather
data was (per #238's own notes, real-data repros were inconsistent). It builds
one shapely ring, positioned to overhang the map's Mercator domain on both the
west and east sides, that is guaranteed to hit the exact "leftover interior
ring split into two by the domain boundary" branch described above.
"""
import shapely.geometry as sgeom

from atmos_gl.tasks.plotting import WEB_MERCATOR


def _domain_splitting_ring():
    """A LinearRing, in WEB_MERCATOR's own projected coordinate space, shaped as a
    thin horizontal band that extends past the map domain's left AND right edges.
    Intersecting the map's boundary polygon with "domain minus this band" removes
    a full-width strip, splitting the domain into a top piece and a bottom piece --
    i.e. exactly the topology that made cartopy's old _rings_to_multi_polygon
    produce a MultiPolygon and mishandle it (see module docstring)."""
    minx, miny, maxx, maxy = WEB_MERCATOR.domain.bounds
    mid_y = (miny + maxy) / 2
    band_half_height = (maxy - miny) * 0.01
    overhang = (maxx - minx) * 0.05  # past the domain's edges on both sides

    ring = sgeom.LinearRing([
        (minx - overhang, mid_y - band_half_height),
        (maxx + overhang, mid_y - band_half_height),
        (maxx + overhang, mid_y + band_half_height),
        (minx - overhang, mid_y + band_half_height),
        (minx - overhang, mid_y - band_half_height),
    ])
    # _rings_to_multi_polygon's "leftover interior rings" branch (the vulnerable
    # one) is only reached by rings NOT matching is_ccw -- force the opposite
    # orientation so this ring takes that path on its own, no exterior ring needed.
    if ring.is_ccw:
        ring = sgeom.LinearRing(list(ring.coords)[::-1])
    assert not ring.is_ccw
    return ring


def test_antimeridian_wrapping_ring_does_not_raise_sequences_of_multipolygons():
    ring = _domain_splitting_ring()

    result = WEB_MERCATOR._rings_to_multi_polygon([ring], is_ccw=True)

    assert result.geom_type == "MultiPolygon"
    assert len(result.geoms) == 2  # the domain split cleanly into top + bottom
