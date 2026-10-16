#!/usr/bin/env python3
"""Shared plain-text collapse for free-text fields sourced from external feeds/APIs
before they're stored -- extracted from collectors/volcanoes.py's original
_strip_html() so AIS (ship_adapter.py) and ADS-B (aircraft_adapter.py) don't each grow
their own copy of the same regex.

This is a secondary hygiene layer, not the XSS-blocking control: the actual defense is
escaping at the frontend render sink (ui/modules/_feedhelpers.js's escapeHtml(), used by
every popup template). strip_html() exists because some sources (GVP's GeoRSS
<description>) deliver real HTML markup that would otherwise break a popup's layout
(see volcanoes.py's docstring) -- for AIS/ADS-B free text (ship/aircraft name,
destination, callsign, registration), it's defense in depth: strips anything
tag-shaped so a value never reaches storage looking like markup, even though the
frontend's escaping is what actually neutralizes it either way.
"""
import re

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str | None:
    """Removes anything tag-shaped and collapses whitespace. Empty/whitespace-only
    input (e.g. a field that was ONLY a tag) collapses to None, matching the "nothing
    useful here" convention every caller's own None-means-absent handling expects."""
    if not text:
        return text
    collapsed = " ".join(_TAG_RE.sub(" ", text).split())
    return collapsed or None
