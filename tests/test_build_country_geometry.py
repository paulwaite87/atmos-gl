#!/usr/bin/env python3
"""tools/build_country_geometry.py: the code each Natural Earth country polygon is keyed
by, and the coordinate rounding that keeps the bundled file small."""
import os
import sys

sys.path.append(os.path.join(os.getcwd(), "tools"))
from build_country_geometry import _geometry, country_code


def test_the_iso_code_is_used_where_natural_earth_has_one():
    assert country_code({"ISO_A3_EH": "FRA", "ADM0_A3": "FRA"}) == "FRA"


def test_countries_without_an_iso_code_fall_back_to_adm0():
    assert country_code({"ISO_A3_EH": "-99", "ADM0_A3": "KOS"}) == "KOS"


def test_rounding_drops_repeated_points_and_collapsed_rings():
    square = [[0.001, 0.001], [1.001, 0.004], [1.0, 1.0], [0.0, 1.0], [0.001, 0.001]]
    sliver = [[5.0, 5.0], [5.001, 5.0], [5.0, 5.001], [5.0, 5.0]]
    geometry = _geometry({"type": "MultiPolygon", "coordinates": [[square], [sliver]]})
    assert geometry == {"type": "Polygon",
                        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]}
