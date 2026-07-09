#!/usr/bin/env python3
"""Tests for _parse_hour_output_name/_is_orphaned (architecture review candidate
"split the orphan-detection predicate out of its I/O shell"). This logic previously
lived entirely inside prune_orphaned_hour_outputs, tangled with glob/os.remove I/O, so
it could only be exercised by touching a real filesystem. Neither function needs a
Housekeeper instance or any I/O -- module-level, pure, previously untested.
"""
from atmos_gl.housekeeper import _parse_hour_output_name, _is_orphaned


# ---- _parse_hour_output_name --------------------------------------------------

def test_parses_static_png_output():
    assert _parse_hour_output_name("precipitation_f003.png") == ("precipitation", 3)


def test_parses_data_texture_output():
    assert _parse_hour_output_name("currents_f019_data.png") == ("currents", 19)


def test_parses_labels_geojson_output():
    assert _parse_hour_output_name("isobars_f012_labels.geojson") == ("isobars", 12)


def test_base_output_with_no_hour_segment_does_not_match():
    """currents.png / currents_key.png have no '_f{NNN}' segment -- safe by
    construction, never eligible for this sweep."""
    assert _parse_hour_output_name("currents.png") is None
    assert _parse_hour_output_name("currents_key.png") is None


def test_malformed_hour_digit_count_does_not_match():
    assert _parse_hour_output_name("currents_f1234.png") is None
    assert _parse_hour_output_name("currents_f19.png") is None  # only 2 digits


def test_unrelated_filename_does_not_match():
    assert _parse_hour_output_name("waves_key.png") is None
    assert _parse_hour_output_name("random_file.txt") is None


# ---- _is_orphaned ---------------------------------------------------------------

def test_unmanaged_layer_is_never_orphaned():
    """A layer absent from the catalog entirely is left alone, regardless of what
    hours are 'live' -- an unmanaged source's files must never be touched."""
    assert _is_orphaned("stray_layer", 5, known_products=set(), live=set()) is False


def test_managed_layer_with_live_hour_is_not_orphaned():
    known = {"currents"}
    live = {("currents", 19), ("currents", 20)}
    assert _is_orphaned("currents", 19, known, live) is False


def test_managed_layer_with_stale_hour_is_orphaned():
    known = {"currents"}
    live = {("currents", 20)}  # 19 aged out of the live window
    assert _is_orphaned("currents", 19, known, live) is True


def test_matches_across_any_live_run_not_just_the_latest():
    """Safe during run transitions -- a file backed by ANY live (layer, fhour) pair
    is kept, not just the one from the most recent run."""
    known = {"waves"}
    live = {("waves", 19)}
    assert _is_orphaned("waves", 19, known, live) is False
