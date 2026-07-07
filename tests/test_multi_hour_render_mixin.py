#!/usr/bin/env python3
"""Tests for the Updater / MultiHourRenderMixin split (architecture review candidate
"slim Updater"). get_output_path_for_hour/publish_current_hour/should_plot_for_hour/
render_all_hours moved off Updater itself onto a mixin that only multi-hour layers
(isobars, wind, precipitation, currents, waves, the scalar-field trio) inherit --
single-shot layers (sst, clouds, markers) no longer see them at all. These 4 methods'
own logic is unchanged (a verbatim move); these tests lock the architectural split
itself and smoke-test the mixin still works when mixed into a bare instance.
"""
import os

from worldmap.tasks.common import Updater, MultiHourRenderMixin, ForecastState


def test_updater_itself_does_not_have_the_per_hour_methods():
    for name in (
        "render_all_hours",
        "should_plot_for_hour",
        "publish_current_hour",
        "get_output_path_for_hour",
    ):
        assert not hasattr(Updater, name), f"Updater should not define {name}"


def test_updater_still_has_get_db_field_at_hour():
    """get_db_field_at_hour stays on Updater itself (not the mixin) -- markers.py, a
    single-shot layer, calls it directly to sample weather at a specific hour, not to
    render a per-hour output."""
    assert hasattr(Updater, "get_db_field_at_hour")


def test_mixin_exposes_exactly_the_four_per_hour_methods():
    own_methods = {
        name
        for name in vars(MultiHourRenderMixin)
        if not name.startswith("__") and callable(getattr(MultiHourRenderMixin, name))
    }
    assert own_methods == {
        "render_all_hours",
        "should_plot_for_hour",
        "publish_current_hour",
        "get_output_path_for_hour",
    }


class _MultiHourLayer(Updater, MultiHourRenderMixin):
    pass


def make_bare_multi_hour_layer(output_path, per_hour_outputs=None):
    u = _MultiHourLayer.__new__(_MultiHourLayer)
    u.section = "test"
    u.output_path = output_path
    u.per_hour_outputs = per_hour_outputs or [".png"]
    return u


def test_get_output_path_for_hour_requires_an_explicit_hour(tmp_path):
    """fhour has no self-fallback (architecture review candidate "ForecastState full
    thread-through") -- every caller passes it explicitly now."""
    u = make_bare_multi_hour_layer(str(tmp_path / "isobars.png"))
    try:
        u.get_output_path_for_hour()
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError: fhour is a required argument")


def test_get_output_path_for_hour_accepts_explicit_hour(tmp_path):
    u = make_bare_multi_hour_layer(str(tmp_path / "isobars.png"))
    assert u.get_output_path_for_hour(12) == str(tmp_path / "isobars_f012.png")


def test_should_plot_for_hour_true_when_output_missing(tmp_path):
    u = make_bare_multi_hour_layer(str(tmp_path / "isobars.png"))
    state = ForecastState.at_hour("2026-06-13", "18", 3)
    assert u.should_plot_for_hour(state, "isobars") is True


def test_publish_current_hour_copies_per_hour_output_to_base_name(tmp_path):
    base = str(tmp_path / "isobars.png")
    u = make_bare_multi_hour_layer(base)
    per_hour_path = tmp_path / "isobars_f003.png"
    per_hour_path.write_bytes(b"fake-png-bytes")

    u.publish_current_hour(3)

    assert os.path.exists(base)
    assert (tmp_path / "isobars.png").read_bytes() == b"fake-png-bytes"
