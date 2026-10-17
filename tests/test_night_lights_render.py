#!/usr/bin/env python3
"""stretch_to_rgba (tasks/night_lights.py): the pure log-stretch + warm ramp over the
8-bit GIBS radiance levels. Expected values are written out, not recomputed."""

import numpy as np

from terrascope.tasks.night_lights import stretch_to_rgba


def px(level, floor=10, alpha=255):
    a = np.array([[[level, level, level, alpha]]], dtype=np.uint8)
    return stretch_to_rgba(a, noise_floor=floor)[0, 0]


def test_levels_at_or_below_the_noise_floor_are_fully_transparent():
    assert px(0)[3] == 0
    assert px(10)[3] == 0
    assert px(11)[3] > 0


def test_faint_light_is_deep_orange():
    r, g, b, a = px(11)
    assert r > 150 and g < 110 and b < 30


def test_the_brightest_level_is_near_white_and_opaque():
    r, g, b, a = px(255)
    assert min(r, g, b) >= 230 and a == 255


def test_brighter_levels_are_more_opaque_and_whiter():
    dim, mid, bright = px(20), px(80), px(200)
    assert dim[3] < mid[3] < bright[3]
    assert dim[2] < mid[2] < bright[2]


def test_the_stretch_is_logarithmic_not_linear():
    # linearly, level 60 would sit ~21% of the way from floor 10 to 255; a log stretch
    # puts it well past half way, so it is already clearly yellower than deep orange
    assert px(60)[1] > 120


def test_no_data_pixels_stay_transparent_whatever_their_level():
    assert px(200, alpha=0)[3] == 0


def test_a_plain_grayscale_array_is_accepted():
    out = stretch_to_rgba(np.array([[5, 255]], dtype=np.uint8), noise_floor=10)
    assert out.shape == (1, 2, 4)
    assert out[0, 0, 3] == 0 and out[0, 1, 3] == 255


# --- NightLightsUpdater.run(): cache -> texture + date sidecar ----------------------
import json
import os
import time
from unittest.mock import MagicMock

from PIL import Image

from terrascope.lib.gibs import night_lights_cache_path, write_night_lights_date
from terrascope.tasks.night_lights import NightLightsUpdater


def make_updater(workdir, settings=None):
    u = NightLightsUpdater.__new__(NightLightsUpdater)
    u.settings = {"noise_floor": 10, **(settings or {})}
    u.section = "night_lights"
    u.workdir = workdir
    u.output_path = os.path.join(workdir, "data", "night_lights.png")
    u.cache_output_path = night_lights_cache_path(workdir)
    u._write_meta_sidecar = lambda f, k, v: json.dump(
        {k: v}, open(os.path.join(workdir, "data", f), "w")
    )
    return u


def seed(workdir, date="2026-10-15"):
    os.makedirs(os.path.join(workdir, "data"), exist_ok=True)
    Image.fromarray(
        np.array([[[5, 5, 5, 255], [255, 255, 255, 255]]], dtype=np.uint8)
    ).save(night_lights_cache_path(workdir))
    write_night_lights_date(workdir, date)


def test_run_writes_the_texture_and_the_date_sidecar(tmp_path):
    seed(str(tmp_path))
    make_updater(str(tmp_path)).run()
    out = np.array(Image.open(tmp_path / "data" / "night_lights.png"))
    assert out[0, 0, 3] == 0 and out[0, 1, 3] == 255
    assert json.load(open(tmp_path / "data" / "night_lights_meta.json")) == {
        "date": "2026-10-15"
    }


def test_run_without_a_cache_does_nothing(tmp_path):
    os.makedirs(tmp_path / "data")
    make_updater(str(tmp_path)).run()
    assert not (tmp_path / "data" / "night_lights.png").exists()


def test_an_unchanged_cache_and_settings_are_not_rendered_again(tmp_path):
    seed(str(tmp_path))
    make_updater(str(tmp_path)).run()
    out = tmp_path / "data" / "night_lights.png"
    t = time.time() - 100
    os.utime(out, (t, t))
    os.utime(night_lights_cache_path(str(tmp_path)), (t - 10, t - 10))
    make_updater(str(tmp_path)).run()
    assert abs(os.path.getmtime(out) - t) < 1


def test_changing_the_noise_floor_re_renders_the_texture(tmp_path):
    seed(str(tmp_path))
    make_updater(str(tmp_path)).run()
    # level 5 is below floor 10 (transparent); a floor of 1 makes it visible
    make_updater(str(tmp_path), {"noise_floor": 1}).run()
    out = np.array(Image.open(tmp_path / "data" / "night_lights.png"))
    assert out[0, 0, 3] > 0
