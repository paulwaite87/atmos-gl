#!/usr/bin/env python3
"""Tests for lib/gfs.py's ATMOS_TARGETS / gfs_index_ranges -- previously uncovered
(#180: extending the shared byte-range fetch with jet-core 250mb UGRD/VGRD targets).

The .idx fixture below is a trimmed excerpt of a real GFS pgrb2.0p25 .idx sidecar
(fetched live and inspected to confirm the exact level label GFS uses -- "250 mb",
not "250mb" or any other variant), so gfs_index_ranges is exercised against realistic
input, not an invented shape.
"""
from unittest.mock import patch, MagicMock

from atmos_gl.lib.gfs import ATMOS_TARGETS, gfs_index_ranges


IDX_FIXTURE = "\n".join(
    [
        "1:0:d=2026072300:PRMSL:mean sea level:24 hour fcst:",
        "11:4881855:d=2026072300:UGRD:planetary boundary layer:24 hour fcst:",
        "260:209169440:d=2026072300:UGRD:200 mb:24 hour fcst:",
        "261:209760275:d=2026072300:VGRD:200 mb:24 hour fcst:",
        "276:219700223:d=2026072300:UGRD:250 mb:24 hour fcst:",
        "277:220306376:d=2026072300:VGRD:250 mb:24 hour fcst:",
        "292:230768855:d=2026072300:UGRD:300 mb:24 hour fcst:",
        "588:431003038:d=2026072300:UGRD:10 m above ground:24 hour fcst:",
        "589:431985607:d=2026072300:VGRD:10 m above ground:24 hour fcst:",
        "666:485853115:d=2026072300:UGRD:tropopause:24 hour fcst:",
    ]
)


def _mock_response():
    resp = MagicMock()
    resp.text = IDX_FIXTURE
    resp.raise_for_status = MagicMock()
    return resp


def test_atmos_targets_includes_jetstream_250mb_entries():
    assert ":UGRD:250 mb:" in ATMOS_TARGETS
    assert ":VGRD:250 mb:" in ATMOS_TARGETS


@patch("atmos_gl.lib.gfs.requests.get", return_value=_mock_response())
def test_gfs_index_ranges_resolves_jetstream_targets(mock_get):
    ranges = gfs_index_ranges(
        "https://example/gfs.t00z.pgrb2.0p25.f024",
        [":UGRD:250 mb:", ":VGRD:250 mb:"],
    )

    assert ranges == [(219700223, 220306375), (220306376, 230768854)]


@patch("atmos_gl.lib.gfs.requests.get", return_value=_mock_response())
def test_gfs_index_ranges_does_not_confuse_250mb_with_other_levels(mock_get):
    """':UGRD:250 mb:' must not match the 200mb or planetary-boundary-layer lines --
    a naive substring target (e.g. just '250') would."""
    ranges = gfs_index_ranges(
        "https://example/gfs.t00z.pgrb2.0p25.f024", [":UGRD:250 mb:"]
    )

    assert ranges == [(219700223, 220306375)]


@patch("atmos_gl.lib.gfs.requests.get", return_value=_mock_response())
def test_gfs_index_ranges_still_resolves_pre_existing_wind_targets(mock_get):
    """Regression guard: the new jet-core targets must not disturb resolution of the
    existing 10m-wind targets sharing the same .idx file."""
    ranges = gfs_index_ranges(
        "https://example/gfs.t00z.pgrb2.0p25.f024",
        [":UGRD:10 m above ground:", ":VGRD:10 m above ground:"],
    )

    assert ranges == [(431003038, 431985606), (431985607, 485853114)]
