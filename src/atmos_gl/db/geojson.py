#!/usr/bin/env python3
"""Shared GeoJSON FeatureCollection envelope, split out of six adapters that each
hand-built it identically (architecture review candidate: GeoJSON FeatureCollection
envelope hand-built per adapter). Only the envelope -- wrap a per-row `feature`
expression into "type": "FeatureCollection" with a coalesced-to-empty jsonb_agg -- is
genuinely identical across quake/volcano/marker/storm/lightning/ship adapters; the
properties list and filter predicate stay adapter-specific, as does the Session/
try-except query-execution dance (each adapter's tests patch Session at that
adapter's own module, so moving Session creation here would break that convention).
"""
from sqlalchemy import func, text

EMPTY_FEATURE_COLLECTION = '{"type":"FeatureCollection","features":[]}'


def as_feature_collection(feature_expr):
    """Wrap a per-row `feature` SQLAlchemy expression (a jsonb_build_object("type",
    "Feature", ...) column expression) into a FeatureCollection envelope: aggregate
    into a jsonb array, falling back to an empty array when there are no rows."""
    return func.jsonb_build_object(
        "type",
        "FeatureCollection",
        "features",
        func.coalesce(func.jsonb_agg(feature_expr), text("'[]'::jsonb")),
    )
