#!/usr/bin/env python3
"""Tests for VectorFieldUpdater (#182) -- the shared base extracted from CurrentsUpdater
once JetStreamUpdater turned out to need the exact same shape. Covers the control flow
the base owns (run()'s call sequence, the abstract-hook contract); CurrentsUpdater/
JetStreamUpdater's own tests cover their per-layer overrides (VMAX, plot()) and serve
as this extraction's regression guard. The palette + legend key are entirely
client-side now (issue #302) -- this base no longer knows about palettes at all.
"""
from unittest.mock import MagicMock

import pytest

from atmos_gl.tasks.vector_field import VectorFieldUpdater


def make_bare_base():
    u = VectorFieldUpdater.__new__(VectorFieldUpdater)
    u.settings = {}
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
    u.render_all_hours = MagicMock(return_value=3)
    return u


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
