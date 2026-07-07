#!/usr/bin/env python3
"""Tests for MultiHourRenderMixin.render_all_hours (architecture review candidate
"ForecastState full thread-through"). This method just underwent its biggest rewrite
of that candidate -- no more self.run_date_str/run_id/forecast_hour_str mutation or
try/finally restore, a fresh ForecastState built per hour, and plot_fn's signature
changed to plot_fn(field, state) -- and had zero direct test coverage before this
(only exercised indirectly through subclass tests that mock plot() itself).
"""
from unittest.mock import MagicMock

from worldmap.tasks.common import Updater, MultiHourRenderMixin, ForecastState


class _MultiHourLayer(Updater, MultiHourRenderMixin):
    pass


def make_bare_layer(hours_resolved=("2026-06-13", "18", [0, 3, 6])):
    u = _MultiHourLayer.__new__(_MultiHourLayer)
    u.section = "test"
    u.output_path = "/tmp/out/test.png"
    u.per_hour_outputs = [".png"]
    u.latest_store_run = MagicMock(return_value=hours_resolved)
    u.process_status_adapter = MagicMock()
    u.publish_current_hour = MagicMock()
    return u


def test_builds_a_fresh_state_per_hour_and_passes_it_to_plot_fn():
    u = make_bare_layer()
    u.should_plot_for_hour = MagicMock(return_value=True)
    u.get_db_field_at_hour = MagicMock(return_value={"values": [[1.0]]})
    plot_fn = MagicMock()

    plotted = u.render_all_hours("isobars", plot_fn, field_ready=lambda f: True)

    assert plotted == 3
    seen_hours = [call.args[1].fhour for call in plot_fn.call_args_list]
    assert seen_hours == [0, 3, 6]
    for call in plot_fn.call_args_list:
        state = call.args[1]
        assert isinstance(state, ForecastState)
        assert state.run_date_str == "2026-06-13"
        assert state.run_id == "18"


def test_skips_hours_that_are_already_fresh():
    u = make_bare_layer()
    u.should_plot_for_hour = MagicMock(side_effect=lambda state, product: state.fhour != 3)
    u.get_db_field_at_hour = MagicMock(return_value={"values": [[1.0]]})
    plot_fn = MagicMock()

    plotted = u.render_all_hours("isobars", plot_fn, field_ready=lambda f: True)

    assert plotted == 2
    seen_hours = {call.args[1].fhour for call in plot_fn.call_args_list}
    assert seen_hours == {0, 6}


def test_skips_hours_whose_field_is_not_ready():
    u = make_bare_layer()
    u.should_plot_for_hour = MagicMock(return_value=True)
    u.get_db_field_at_hour = MagicMock(return_value={"u": None})
    plot_fn = MagicMock()

    plotted = u.render_all_hours("wind", plot_fn, field_ready=lambda f: f.get("u") is not None)

    assert plotted == 0
    plot_fn.assert_not_called()


def test_one_hour_plot_failure_does_not_stop_the_others():
    u = make_bare_layer()
    u.should_plot_for_hour = MagicMock(return_value=True)
    u.get_db_field_at_hour = MagicMock(return_value={"values": [[1.0]]})

    def flaky_plot(field, state):
        if state.fhour == 3:
            raise RuntimeError("boom")

    plotted = u.render_all_hours("isobars", flaky_plot, field_ready=lambda f: True)

    assert plotted == 2  # hours 0 and 6 still rendered despite hour 3 raising


def test_publishes_the_last_hour_when_any_hours_were_resolved():
    u = make_bare_layer()
    u.should_plot_for_hour = MagicMock(return_value=False)  # nothing needs plotting
    u.get_db_field_at_hour = MagicMock()
    plot_fn = MagicMock()

    u.render_all_hours("isobars", plot_fn, field_ready=lambda f: True)

    u.publish_current_hour.assert_called_once_with(6)  # last of [0, 3, 6]


def test_no_catalog_data_returns_zero_and_does_not_publish():
    u = make_bare_layer(hours_resolved=None)
    plot_fn = MagicMock()

    plotted = u.render_all_hours("isobars", plot_fn, field_ready=lambda f: True)

    assert plotted == 0
    plot_fn.assert_not_called()
    u.publish_current_hour.assert_not_called()


def test_no_instance_state_is_mutated():
    """The whole point of full thread-through: render_all_hours must not read or write
    self.run_date_str/run_id/forecast_hour_str at all."""
    u = make_bare_layer()
    u.should_plot_for_hour = MagicMock(return_value=True)
    u.get_db_field_at_hour = MagicMock(return_value={"values": [[1.0]]})

    u.render_all_hours("isobars", MagicMock(), field_ready=lambda f: True)

    assert not hasattr(u, "run_date_str")
    assert not hasattr(u, "run_id")
    assert not hasattr(u, "forecast_hour_str")
