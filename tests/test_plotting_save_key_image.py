#!/usr/bin/env python3
"""Tests for PlottingMixin.save_key_image, the shared colourbar-key renderer absorbed
from the near-identical save_*_key methods in ozone.py/temperature.py/stormwatch.py/
precipitation.py/currents.py/sst.py/waves.py (see the architecture review's "Absorb
the colorbar-key renderer" candidate). Split out of tasks/common.py into
tasks/plotting.py (architecture review candidate "tasks/common.py bundles six
unrelated concerns"); tested directly against PlottingMixin, its real home, rather
than through the Updater subclass that mixes it in.
"""
import os

import matplotlib.colors as mcolors

from atmos_gl.tasks.plotting import PlottingMixin


def make_bare_mixin():
    mixin = PlottingMixin.__new__(PlottingMixin)
    mixin.section = "test"
    return mixin


def test_save_key_image_writes_key_png_at_base_name(tmp_path):
    mixin = make_bare_mixin()
    output_path = str(tmp_path / "layer_f003.png")
    cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("viridis")
    norm = mcolors.Normalize(vmin=0.0, vmax=1.0)

    mixin.save_key_image(output_path, cmap, norm, [0.0, 0.5, 1.0], "Test (units)")

    assert os.path.exists(str(tmp_path / "layer_f003_key.png"))


def test_save_key_image_applies_tick_format_and_weight(tmp_path):
    mixin = make_bare_mixin()
    output_path = str(tmp_path / "currents.png")
    cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("magma")
    norm = mcolors.Normalize(vmin=0.0, vmax=2.5)

    # Should not raise -- exercises the tick_format + weight code paths that only
    # currents/sst/waves use.
    mixin.save_key_image(
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
    mixin = make_bare_mixin()
    output_path = str(tmp_path / "waves.png")
    cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("cool")
    norm = mcolors.Normalize(vmin=0.0, vmax=8.0)

    calls = []

    def decorate(cbar):
        calls.append(cbar)
        cbar.ax.axvline(2.0, color="white", linewidth=1.2)

    mixin.save_key_image(
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
