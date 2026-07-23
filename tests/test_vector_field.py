#!/usr/bin/env python3
"""Tests for VectorFieldUpdater (#182) -- the shared base extracted from CurrentsUpdater
once JetStreamUpdater turned out to need the exact same shape. Covers the control flow
the base owns (run()'s call sequence, the abstract-hook contract) and _palette()'s
validation logic generically; CurrentsUpdater/JetStreamUpdater's own tests cover their
per-layer overrides (VMAX, PALETTES, plot()) and serve as this extraction's regression
guard.
"""
from unittest.mock import MagicMock

import pytest

from atmos_gl.tasks.vector_field import VectorFieldUpdater


def make_bare_base():
    u = VectorFieldUpdater.__new__(VectorFieldUpdater)
    u.settings = {}
    u.PALETTES = {"a": [(0, 0, 0)], "b": [(1, 1, 1)]}
    u.DEFAULT_PALETTE = "a"
    return u


# ---- abstract hooks -------------------------------------------------------------

def test_plot_is_not_implemented_on_the_raw_base():
    u = make_bare_base()
    with pytest.raises(NotImplementedError):
        u.plot(field0={}, state=None)


def test_warm_baseline_cache_is_not_implemented_on_the_raw_base():
    u = make_bare_base()
    with pytest.raises(NotImplementedError):
        u._warm_baseline_cache()


# ---- _palette (generic, subclass-agnostic) ---------------------------------------

def test_palette_uses_the_configured_value_when_valid():
    u = make_bare_base()
    u.settings = {"palette": "b"}
    assert u._palette() == "b"


def test_palette_falls_back_to_default_when_unset():
    u = make_bare_base()
    assert u._palette() == "a"


def test_palette_falls_back_to_default_when_invalid():
    u = make_bare_base()
    u.settings = {"palette": "not-a-real-palette"}
    assert u._palette() == "a"


# ---- run() call sequence ---------------------------------------------------------

def make_run_test_double():
    """A minimal concrete subclass exercising run()'s shared control flow without any
    of CurrentsUpdater/JetStreamUpdater's per-layer plot() logic."""

    class _Concrete(VectorFieldUpdater):
        pass

    u = _Concrete.__new__(_Concrete)
    u.output_path = "/tmp/out/vf.png"
    u.status_product = "vf_test"
    u._warm_baseline_cache = MagicMock()
    u.save_key = MagicMock()
    u.render_all_hours = MagicMock(return_value=3)
    return u


def test_run_warms_baseline_before_saving_the_key():
    u = make_run_test_double()
    call_order = []
    u._warm_baseline_cache.side_effect = lambda: call_order.append("warm")
    u.save_key.side_effect = lambda path: call_order.append("key")

    u.run()

    assert call_order == ["warm", "key"]


def test_run_saves_the_key_at_output_path():
    u = make_run_test_double()
    u.run()
    u.save_key.assert_called_once_with("/tmp/out/vf.png")


def test_run_dispatches_render_all_hours_with_status_product_and_plot():
    u = make_run_test_double()
    u.run(max_hours=1)
    u.render_all_hours.assert_called_once()
    call = u.render_all_hours.call_args
    assert call.args[0] == "vf_test"
    assert call.kwargs["plot_fn"] == u.plot
    assert call.kwargs["max_hours"] == 1


def test_run_returns_render_all_hours_result():
    u = make_run_test_double()
    assert u.run() == 3


def test_field_ready_requires_both_u_and_v():
    u = make_run_test_double()
    u.run()
    field_ready = u.render_all_hours.call_args.kwargs["field_ready"]

    assert field_ready({"u": [1], "v": [1]}) is True
    assert field_ready({"u": [1], "v": None}) is False
    assert field_ready({"u": None, "v": [1]}) is False
    assert field_ready({}) is False
