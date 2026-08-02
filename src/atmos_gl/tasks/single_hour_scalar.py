#!/usr/bin/env python3
"""Shared base for updaters that render one static PNG + one WebGL data texture per
forecast hour, off a single fieldstore product (ScalarFieldUpdater, PrecipitationUpdater).

Only the constructor fields and run() wiring that are genuinely identical between those
two live here -- plot() itself stays a full per-subclass override, not hook-split.
Their plot() bodies diverge almost entirely (different regrid fill_value, color model,
and precipitation's extra smoothing/floor/sqrt-transform pipeline before encode_frames),
so splitting plot() into sub-hooks would produce hooks nearly as large as the original
methods, with no real documentation payoff (see architecture review candidate "give
precipitation the ScalarFieldSpec deepening its frontend already got").

Validated with ast.parse.
"""
from .common import Updater, MultiHourRenderMixin


class SingleHourScalarUpdater(Updater, MultiHourRenderMixin):
    def __init__(self, config, product, map_data):
        super().__init__(config, product, map_data)
        self.level_of_detail = int(self.settings.get("level_of_detail", 1))
        self.lod_desc = None
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = product

    def run(self, max_hours=None):
        # Warms the shared per-cycle GFS baseline cache (map_data.shared_state) for
        # other updaters this cycle; render_all_hours resolves its own state from the
        # catalog below, so the return value here is unused.
        self.get_gfs_state()
        # The legend key is cheap to draw and depends only on palette/threshold/
        # key_fontsize settings, not forecast data. Refresh it unconditionally every
        # run, so settings changes apply immediately instead of waiting on
        # should_plot_for_hour's data-freshness gate below.
        self._write_key()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        # max_hours=1 from layer_builder's round-robin dispatch renders one hour and
        # returns, so this layer doesn't monopolise a render-pool worker.
        return self.render_all_hours(
            self.status_product,
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
            max_hours=max_hours,
        )

    def _write_key(self):
        raise NotImplementedError

    def plot(self, field0, state):
        raise NotImplementedError
