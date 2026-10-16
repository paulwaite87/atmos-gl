#!/usr/bin/env python3
"""tools/build_country_geometry.py: the code each Natural Earth country polygon is keyed
by, and the coordinate rounding that keeps the bundled file small."""

import os
import sys

sys.path.append(os.path.join(os.getcwd(), "tools"))
from shapely.geometry import Point, shape

from build_country_geometry import _geometry, country_code, country_iso2, label_point


def test_the_iso_code_is_used_where_natural_earth_has_one():
    assert country_code({"ISO_A3_EH": "FRA", "ADM0_A3": "FRA"}) == "FRA"


def test_countries_without_an_iso_code_fall_back_to_adm0():
    assert country_code({"ISO_A3_EH": "-99", "ADM0_A3": "KOS"}) == "KOS"


def test_rounding_drops_repeated_points_and_collapsed_rings():
    square = [[0.001, 0.001], [1.001, 0.004], [1.0, 1.0], [0.0, 1.0], [0.001, 0.001]]
    sliver = [[5.0, 5.0], [5.001, 5.0], [5.0, 5.001], [5.0, 5.0]]
    geometry = _geometry({"type": "MultiPolygon", "coordinates": [[square], [sliver]]})
    assert geometry == {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
    }


def test_the_two_letter_iso_code_is_kept_for_layers_whose_feeds_use_it():
    assert country_iso2({"ISO_A2_EH": "LT"}) == "LT"


def test_countries_without_a_two_letter_code_have_none():
    assert country_iso2({"ISO_A2_EH": "-99"}) == ""


def _square(x, y, size):
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]


def test_the_label_point_of_a_multi_part_country_is_on_its_largest_part():
    # A 10-degree mainland and a 1-degree territory far away: the centroid of the two together
    # falls in the sea between them.
    mainland = [_square(0, 0, 10)]
    territory = [_square(100, 0, 1)]
    geometry = {"type": "MultiPolygon", "coordinates": [territory, mainland]}

    lon, lat = label_point(geometry)

    assert shape({"type": "Polygon", "coordinates": mainland}).contains(Point(lon, lat))
    assert (lon, lat) == (5.0, 5.0)


def test_the_label_point_of_a_crescent_is_on_land_not_at_its_centroid():
    # A C-shaped country: its centroid lies in the bay, but the label point must be on land.
    crescent = [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 8], [8, 8], [8, 2], [0, 2], [0, 0]]
    ]
    lon, lat = label_point({"type": "Polygon", "coordinates": crescent})

    assert shape({"type": "Polygon", "coordinates": crescent}).contains(Point(lon, lat))
    assert not Point(lon, lat).equals(
        shape({"type": "Polygon", "coordinates": crescent}).centroid
    )
