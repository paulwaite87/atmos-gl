#!/usr/bin/env python3
"""Tests for CurrentsUpdater, written BEFORE extracting the shared VectorFieldUpdater
base (#182) to lock down current behavior as a regression safety net -- CurrentsUpdater
previously had zero test coverage. Mirrors test_wind.py's bare-updater pattern (bypass
Updater.__init__, wire only what the method under test reads).
"""
from unittest.mock import MagicMock, patch

import numpy as np

from atmos_gl.tasks.currents import CurrentsUpdater


def make_bare_updater(settings=None):
    """Bypass Updater.__init__ (does config/IO); wire only what _palette/save_currents_key
    (or, post-refactor, _palette/save_key) read. PALETTES is set explicitly here (rather
    than relying on __init__) since it's currently an instance attribute -- if the #182
    extraction turns it into a class attribute instead, this override is harmless and
    the test keeps working either way."""
    u = CurrentsUpdater.__new__(CurrentsUpdater)
    u.settings = settings or {}
    u.save_key_image = MagicMock()
    u.status_product = "currents"
    u.PALETTES = {
        "thermal_red": [
            (0.65, 0.0, 0.0),
            (1.0, 0.25, 0.0),
            (1.0, 0.85, 0.0),
            (1.0, 1.0, 1.0),
        ],
        "electric_blue": [(0.0, 0.35, 0.55), (0.0, 0.85, 1.0), (0.75, 1.0, 1.0)],
        "toxic_neon": [(0.0, 0.45, 0.15), (0.25, 1.0, 0.0), (0.95, 1.0, 0.3)],
        "cyberpunk": [(0.45, 0.0, 0.45), (1.0, 0.0, 0.55), (0.0, 1.0, 0.75)],
    }
    return u


# ---- _palette -----------------------------------------------------------------

def test_palette_defaults_to_thermal_red_when_unset():
    u = make_bare_updater()
    assert u._palette() == "thermal_red"


def test_palette_honours_a_valid_configured_choice():
    u = make_bare_updater(settings={"palette": "electric_blue"})
    assert u._palette() == "electric_blue"


def test_palette_falls_back_to_thermal_red_for_an_unknown_name():
    u = make_bare_updater(settings={"palette": "not-a-real-palette"})
    assert u._palette() == "thermal_red"


# ---- save_currents_key ---------------------------------------------------------

def _save_key():
    """Call whichever method name currently exists on CurrentsUpdater -- save_currents_key
    pre-refactor, save_key post-refactor (VectorFieldUpdater's shared method) -- so this
    test file keeps passing across the #182 extraction without being rewritten."""
    u = make_bare_updater()
    method = getattr(u, "save_currents_key", None) or getattr(u, "save_key")
    method("/tmp/out/currents.png")
    return u


def test_save_currents_key_uses_the_fixed_vmax_current_range():
    u = _save_key()
    key_args = u.save_key_image.call_args
    assert key_args.args[0] == "/tmp/out/currents.png"
    assert list(key_args.args[3]) == [0.0, 2.5 / 3, 2.5 * 2 / 3, 2.5]
    assert key_args.args[4] == "Current Speed (m/s)"


def test_save_currents_key_matches_the_shared_key_style():
    u = _save_key()
    key_args = u.save_key_image.call_args
    assert key_args.kwargs["key_fontsize"] == 10
    assert key_args.kwargs["labelsize"] == 8
    assert key_args.kwargs["weight"] == "bold"
    assert key_args.kwargs["tick_format"] == "%.1f"


def test_save_currents_key_honours_a_configured_key_fontsize():
    u = make_bare_updater(settings={"key_fontsize": 14})
    method = getattr(u, "save_currents_key", None) or getattr(u, "save_key")
    method("/tmp/out/currents.png")
    assert u.save_key_image.call_args.kwargs["key_fontsize"] == 14


def test_save_currents_key_uses_the_configured_palette_colors():
    """Regression guard for the extraction: the colourbar must be built from the
    SELECTED palette's colors, not always the default."""
    u = make_bare_updater(settings={"palette": "cyberpunk"})
    with patch("matplotlib.colors.LinearSegmentedColormap.from_list") as mock_from_list:
        mock_from_list.return_value = MagicMock()
        method = getattr(u, "save_currents_key", None) or getattr(u, "save_key")
        method("/tmp/out/currents.png")
        _, args, kwargs = mock_from_list.mock_calls[0]
        colors = kwargs.get("colors") if "colors" in kwargs else args[1]
        assert colors == u.PALETTES["cyberpunk"]


# ---- _land_mask_for -------------------------------------------------------------

def test_land_mask_for_is_cached_per_shape():
    u = make_bare_updater()
    u._land_mask_cache = {}
    sentinel = np.array([[True, False]])
    with patch(
        "atmos_gl.tasks.currents.coastline_land_mask", return_value=sentinel
    ) as mock_coast:
        first = u._land_mask_for(lat=[0.0], lon=[0.0, 1.0], shape=(1, 2))
        second = u._land_mask_for(lat=[0.0], lon=[0.0, 1.0], shape=(1, 2))
        assert first is sentinel
        assert second is sentinel
        mock_coast.assert_called_once()
