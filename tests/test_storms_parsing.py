#!/usr/bin/env python3
"""Tests for StormsCollector._parse_b_deck/_parse_a_deck's intensity extraction
(WIND_KT/PRESSURE_HPA/CATEGORY -- ATCF fields 8/9/10) -- these sit at the same fixed
positions in both deck types, alongside the LAT/LON/TIME fields already parsed, but
were previously discarded entirely (no popup row, no DB column)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.storms import StormsCollector


def make_collector():
    return StormsCollector.__new__(StormsCollector)


def _fake_response(text):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = text
    return resp


# A real-shaped BEST-track line (NHC btk format) -- SID/NAME come from the filename and
# field 27 respectively, elsewhere in _parse_b_deck; this test only cares about fields
# 8 (VMAX, knots), 9 (MSLP, hPa), and 10 (TY, category code).
B_DECK_LINE = (
    "EP, 01, 2026060800,   , BEST,   0, 113N, 1360W,  25, 1007, LO,  34, NEQ,    0,"
    "    0,    0,    0, 1013,  120,  25,  40,   0,   E,   0,    ,   0,   0,     AMANDA,"
    " S,  0,    ,    0,    0,    0,    0, genesis-num, 001,"
)


def test_parse_b_deck_extracts_wind_pressure_and_category():
    c = make_collector()
    # expiry_days is irrelevant to what this test checks (field extraction) -- pass a
    # huge value so B_DECK_LINE's fixed date never trips the "too old" check.
    with patch("atmos_gl.collectors.storms.requests.get", return_value=_fake_response(B_DECK_LINE)):
        pts = c._parse_b_deck(
            "http://example/bep012026.dat", datetime.now(timezone.utc), expiry_days=999999
        )

    assert len(pts) == 1
    assert pts[0]["WIND_KT"] == 25
    assert pts[0]["PRESSURE_HPA"] == 1007
    assert pts[0]["CATEGORY"] == "LO"


def test_parse_a_deck_extracts_wind_pressure_and_category():
    # OFCL forecast line, TAU=24 (not 0, so _parse_a_deck keeps it).
    a_deck_line = B_DECK_LINE.replace("BEST", "OFCL").replace("   0, 113N", "  24, 113N")
    c = make_collector()
    with patch("atmos_gl.collectors.storms.requests.get", return_value=_fake_response(a_deck_line)):
        pts = c._parse_a_deck("http://example/aep012026.dat", "EP012026")

    assert len(pts) == 1
    assert pts[0]["WIND_KT"] == 25
    assert pts[0]["PRESSURE_HPA"] == 1007
    assert pts[0]["CATEGORY"] == "LO"


def test_parse_intensity_returns_none_for_blank_fields():
    c = make_collector()
    parts = ["EP", "01", "2026060800", "", "BEST", "0", "113N", "1360W", "", "", ""]

    wind_kt, pressure_hpa, category = c._parse_intensity(parts)

    assert wind_kt is None
    assert pressure_hpa is None
    assert category is None
