#!/usr/bin/env python3
"""Tests for Updater.save_key_image, the shared colourbar-key renderer absorbed from
the near-identical save_*_key methods in ozone.py/temperature.py/stormwatch.py/
precipitation.py/currents.py/sst.py/waves.py (see the architecture review's "Absorb
the colorbar-key renderer" candidate).
"""
import os

import matplotlib.colors as mcolors

from worldmap.tasks.common import Updater


def make_bare_updater():
    updater = Updater.__new__(Updater)
    updater.section = "test"
    return updater


def test_save_key_image_writes_key_png_at_base_name(tmp_path):
    updater = make_bare_updater()
    output_path = str(tmp_path / "layer_f003.png")
    cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("viridis")
    norm = mcolors.Normalize(vmin=0.0, vmax=1.0)

    updater.save_key_image(output_path, cmap, norm, [0.0, 0.5, 1.0], "Test (units)")

    assert os.path.exists(str(tmp_path / "layer_f003_key.png"))


def test_save_key_image_applies_tick_format_and_weight(tmp_path):
    updater = make_bare_updater()
    output_path = str(tmp_path / "currents.png")
    cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("magma")
    norm = mcolors.Normalize(vmin=0.0, vmax=2.5)

    # Should not raise -- exercises the tick_format + weight code paths that only
    # currents/sst/waves use.
    updater.save_key_image(
        output_path,
        cmap,
        norm,
        [0.0, 1.0, 2.0],
        "Current Speed (m/s)",
        key_fontsize=10,
        labelsize=8,
        tick_format="%.1f",
        weight="bold",
    )

    assert os.path.exists(str(tmp_path / "currents_key.png"))


def test_save_key_image_calls_decorate_hook_before_styling(tmp_path):
    updater = make_bare_updater()
    output_path = str(tmp_path / "waves.png")
    cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("cool")
    norm = mcolors.Normalize(vmin=0.0, vmax=8.0)

    calls = []

    def decorate(cbar):
        calls.append(cbar)
        cbar.ax.axvline(2.0, color="white", linewidth=1.2)

    updater.save_key_image(
        output_path,
        cmap,
        norm,
        [0, 2, 4, 6, 8],
        "Wave Height (m) ≥ 2",
        key_fontsize=10,
        labelsize=8,
        weight="bold",
        decorate=decorate,
    )

    assert len(calls) == 1
    assert os.path.exists(str(tmp_path / "waves_key.png"))
