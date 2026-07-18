#!/usr/bin/env python3
import argparse
import logging
import sys
import os
import signal
import asyncio
import multiprocessing
from functools import partial
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

# Library imports
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.logging import setup_logging, set_loglevel
from atmos_gl.db.process_status_adapter import ProcessStatusAdapter


# Task imports
from atmos_gl.tasks.common import MapData, LAYER_CYCLE_SECONDS, MultiHourRenderMixin
from atmos_gl.tasks.clouds import CloudUpdater
from atmos_gl.tasks.isobars import IsobarUpdater
from atmos_gl.tasks.polar_boundary import PolarBoundaryUpdater
from atmos_gl.tasks.wind import WindUpdater
from atmos_gl.tasks.precipitation import PrecipitationUpdater
from atmos_gl.tasks.sst import SSTUpdater
from atmos_gl.tasks.currents import CurrentsUpdater
from atmos_gl.tasks.waves import WavesUpdater
from atmos_gl.tasks.scalar_field import ScalarFieldUpdater, SPECS
from atmos_gl.tasks.markers import MarkerUpdater
from atmos_gl.tasks.fire_weather import FireWeatherUpdater

logger = logging.getLogger("atmos_gl.layer_builder")

# Seconds between fan-out cycles. Every cycle dispatches all updaters; per-hour freshness
# checks make a steady-state (nothing-changed) cycle cheap, so this is just the
# responsiveness window for picking up new data or deleted output. Canonical definition
# is tasks.common.LAYER_CYCLE_SECONDS (Updater.layer_status() needs it too, and
# tasks/common.py can't import this module without a cycle).
CYCLE_SECONDS = LAYER_CYCLE_SECONDS

# section -> updater class. The parent dispatches one task per entry; each worker process
# looks up the class it must build by section name. Order is informational only now —
# updaters render in parallel, not in sequence.
TASK_CLASSES = {
    "isobars": IsobarUpdater,
    "polar_boundary": PolarBoundaryUpdater,
    "precipitation": PrecipitationUpdater,
    "clouds": CloudUpdater,
    "wind": WindUpdater,
    "sst": SSTUpdater,
    "currents": CurrentsUpdater,
    "waves": WavesUpdater,
    "temperature": partial(ScalarFieldUpdater, spec=SPECS["temperature"]),
    "ozone": partial(ScalarFieldUpdater, spec=SPECS["ozone"]),
    "stormwatch": partial(ScalarFieldUpdater, spec=SPECS["stormwatch"]),
    "pwat": partial(ScalarFieldUpdater, spec=SPECS["pwat"]),
    "markers": MarkerUpdater,
    "fires": FireWeatherUpdater,
}


def _updater_class(entry):
    """The plain class behind a TASK_CLASSES entry, unwrapping the partial() binding
    the four ScalarFieldUpdater-based sections use."""
    return entry.func if isinstance(entry, partial) else entry


# Sections rendered per-forecast-hour (mix in MultiHourRenderMixin) vs. once per cycle
# (sst/clouds/markers -- no per-hour concept). Only multi-hour sections participate in
# the round-robin dispatch below: each round renders at most ONE hour per section, so a
# section with a large backlog can't monopolise the render pool's workers for its whole
# catch-up -- every section advances roughly evenly instead of depth-first through
# whichever ones got dispatched first (architecture review candidate "interleave
# per-hour rendering across layers").
MULTI_HOUR_SECTIONS = [
    name for name, entry in TASK_CLASSES.items()
    if issubclass(_updater_class(entry), MultiHourRenderMixin)
]
SINGLE_SHOT_SECTIONS = [name for name in TASK_CLASSES if name not in MULTI_HOUR_SECTIONS]


def _worker_init(config_path):
    """Runs once per worker PROCESS at spawn. The child never calls main(), so it must
    configure its own logging — at the configured level so worker render logs match the
    parent's verbosity."""
    setup_logging()
    try:
        level = AtmosGLConfig(config_path).get_setting("common", "log_level")
        if level:
            set_loglevel(level)
    except Exception:
        pass


def _render_worker(config_path, section, baseline, max_hours=None):
    """Runs in a SEPARATE PROCESS.

    Rebuilds config + map_data from the config path (no live objects cross the process
    boundary, and config edits are picked up automatically), injects the pre-resolved
    GFS/RTOFS baseline so the worker never re-probes NOMADS, then builds the one updater
    for `section` and renders it.

    Each process owns its own cartopy / matplotlib / GEOS state, so renders run truly in
    parallel — what the thread model could not do safely (those C libraries are not
    thread-safe and segfaulted under concurrency).

    max_hours is forwarded to run() unconditionally -- every TASK_CLASSES updater
    accepts it now (single-shot layers ignore it; multi-hour ones cap the backlog they
    drain this call to that many hours). Returns (section, error, plotted): error is
    None on success (repr(exception) on failure, and one failing layer can't poison the
    gather since it's caught here rather than raised); plotted is however many hours
    run() actually rendered (0 for single-shot layers, or an exception).
    """
    try:
        cfg = AtmosGLConfig(config_path)
        md = MapData(cfg)
        md.shared_state = {}
        if baseline.get("gfs"):
            md.shared_state["gfs_baseline"] = baseline["gfs"]
        if baseline.get("rtofs"):
            md.shared_state["rtofs_baseline"] = baseline["rtofs"]
        plotted = TASK_CLASSES[section](cfg, md).run(max_hours=max_hours)
        return (section, None, plotted or 0)
    except Exception as e:
        return (section, repr(e), 0)


class LayerBuilder:
    enabled = False

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = AtmosGLConfig(config_path)
        self.map_data = MapData(self.config)
        # Own ProcessStatusAdapter, used ONLY to record process_status for the Data Status UI
        # after each cycle (see _handle_results). Rendering itself happens in worker
        # processes with their own fieldstore/db connections; this one never touches
        # render data.
        self.process_status_adapter = ProcessStatusAdapter()

        # Ensure this folder exists
        data_dir = os.path.join(
            self.config.get_setting("common", "workdir", "."), "data"
        )
        os.makedirs(data_dir, exist_ok=True)

        # Shared state holds the GFS/RTOFS baseline the primer resolves each cycle.
        self.map_data.shared_state = {}

        signal.signal(signal.SIGUSR1, self.handle_force_refresh)

        # One in-process updater, used ONLY to resolve the baseline once per cycle (a
        # lightweight NOMADS probe, no rendering). All rendering happens in worker
        # processes. Built in start_scheduler once the region/config are current.
        self._primer = None

        # Render is CPU-bound, so cap workers at core count (never more than the number of
        # layers). Tunable here; deliberately not a config knob.
        self._max_workers = min(len(TASK_CLASSES), os.cpu_count() or 4)
        self._pool = None

    def refresh_settings(self):
        self.config.load()
        self.enabled = self.config.get_setting("layer_builder", "enabled")
        # Adjust log level if changed
        log_level = self.config.get_setting("common", "log_level")
        if log_level:
            set_loglevel(log_level)

    def handle_force_refresh(self, signum, frame):
        """SIGUSR1: drop the cached GFS/RTOFS datum so the next cycle re-resolves it."""
        logger.debug("External trigger (SIGUSR1): clearing cached baselines")
        ss = getattr(self.map_data, "shared_state", None)
        if isinstance(ss, dict):
            ss.pop("gfs_baseline", None)
            ss.pop("rtofs_baseline", None)

    def _new_pool(self):
        """Create a fresh spawn-based process pool. 'spawn' (not fork) gives each worker a
        clean interpreter: fork would inherit the parent's GEOS/PROJ/matplotlib state and
        re-introduce the very C-library hazards the process model exists to escape."""
        logger.info(
            f"Starting render process pool (max_workers={self._max_workers}, spawn)"
        )
        return ProcessPoolExecutor(
            max_workers=self._max_workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_worker_init,
            initargs=(self.config_path,),
        )

    def _resolve_baselines(self):
        """Resolve the GFS/RTOFS datums ONCE, up front (cleared first so a long-lived
        process can't pin to an ever-older run), and return them as a plain dict to hand to
        every worker — so all workers inherit one datum instead of each re-probing NOMADS."""
        ss = self.map_data.shared_state
        ss.pop("gfs_baseline", None)
        ss.pop("rtofs_baseline", None)
        for label, resolve in (
            ("GFS", self._primer.get_gfs_state),
            ("RTOFS", self._primer.get_rtofs_state),
        ):
            try:
                resolve()
            except Exception as e:
                logger.warning(f"{label} baseline pre-resolve failed: {e}")
        return {"gfs": ss.get("gfs_baseline"), "rtofs": ss.get("rtofs_baseline")}

    def _handle_results(self, sections, results):
        """Log per-task errors and record process_status for the Data Status UI (one row
        per dispatched section, success or failure). `sections` is the same ordered list
        futures were built from, so zip(sections, results) reliably pairs each result with
        its task even in the edge case where a result is a bare Exception (e.g. the
        executor itself died) rather than _render_worker's own (section, error, plotted)
        tuple.

        Returns (broken, plotted_by_section). broken is True if the pool broke (a worker
        died) and must be recreated. plotted_by_section maps each section to how many
        hours it actually rendered this dispatch -- the round-robin loop in
        start_scheduler() uses it to drop a multi-hour section once it stops reporting
        progress, rather than looping it forever.
        """
        broken = False
        plotted_by_section = {}
        for section, r in zip(sections, results):
            if isinstance(r, BrokenProcessPool):
                broken = True
                self.process_status_adapter.record_process_run(
                    section, "layer", success=False, error="render pool broke"
                )
            elif isinstance(r, Exception):
                logger.error(f"Render dispatch error: {r!r}")
                self.process_status_adapter.record_process_run(
                    section, "layer", success=False, error=repr(r)
                )
            elif r and r[1]:
                logger.error(f"Task '{r[0]}' failed in worker: {r[1]}")
                self.process_status_adapter.record_process_run(
                    section, "layer", success=False, error=r[1]
                )
            else:
                self.process_status_adapter.record_process_run(section, "layer", success=True)
                if r and len(r) > 2:
                    plotted_by_section[section] = r[2] or 0
        if broken:
            logger.error("Render worker died (BrokenProcessPool); recreating pool")
        return broken, plotted_by_section

    async def _dispatch_round(self, loop, sections, baseline, max_hours_by_section):
        """Dispatch one future per section in `sections` (each capped to
        max_hours_by_section[section] hours -- None for single-shot layers, 1 for a
        multi-hour layer's round-robin turn), gather, record process_status, and
        recreate the pool if a worker died. Returns plotted_by_section."""
        futures = [
            loop.run_in_executor(
                self._pool, _render_worker, self.config_path, section, baseline,
                max_hours_by_section[section],
            )
            for section in sections
        ]
        results = await asyncio.gather(*futures, return_exceptions=True)
        broken, plotted_by_section = self._handle_results(sections, results)
        if broken:
            try:
                self._pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._pool = self._new_pool()
        return plotted_by_section

    async def _run_dispatch_cycle(self, loop, baseline):
        """One cycle's worth of rendering, given an already-resolved baseline.

        Single-shot layers (sst/clouds/markers) dispatch once, same as before.
        Multi-hour layers dispatch in ROUNDS -- one hour per section per round -- so a
        section with a large backlog can't monopolise the render pool's workers for its
        whole catch-up; every section advances roughly evenly instead of depth-first
        through whichever ones happened to dispatch first (architecture review
        candidate "interleave per-hour rendering across layers"). A round drops a
        multi-hour section once it stops reporting progress.
        """
        pending = {s: None for s in SINGLE_SHOT_SECTIONS}
        pending.update({s: 1 for s in MULTI_HOUR_SECTIONS})

        while pending:
            sections = list(pending)
            plotted_by_section = await self._dispatch_round(
                loop, sections, baseline, pending
            )
            pending = {
                s: 1 for s in MULTI_HOUR_SECTIONS
                if s in pending and plotted_by_section.get(s, 0) > 0
            }

    async def start_scheduler(self):
        # Initial refresh so the region/config are current before the primer is built.
        self.refresh_settings()
        self.map_data.refresh()
        self._primer = TASK_CLASSES[next(iter(TASK_CLASSES))](self.config, self.map_data)
        self._pool = self._new_pool()
        loop = asyncio.get_running_loop()

        try:
            while True:
                self.refresh_settings()

                if self.enabled:
                    self.map_data.refresh()

                    # Resolve the datum once, then dispatch every updater to its own
                    # process. Workers rebuild config per task, so config edits are picked
                    # up automatically — no rebuild bookkeeping here. No should_run gating;
                    # each updater's per-hour freshness check skips already-current work, so
                    # a steady-state cycle is cheap and a changed/deleted layer re-renders
                    # promptly, now-hour first — and now genuinely in parallel.
                    # refresh_settings/baseline-resolve still only happen once per OUTER
                    # cycle -- a very large backlog still delays picking up config/baseline
                    # changes until every section's rounds finish, unchanged from before
                    # this file's per-hour round-robin dispatch existed.
                    baseline = self._resolve_baselines()
                    await self._run_dispatch_cycle(loop, baseline)
                else:
                    logger.info("Layer-builder scheduler disabled: skipping")

                await asyncio.sleep(CYCLE_SECONDS)
        finally:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser(description="Atmos GL Layer Builder Scheduler")
    parser.add_argument("--config", required=True, help="Path to atmos-gl.json")
    args = parser.parse_args()

    setup_logging()
    layer_builder = LayerBuilder(args.config)

    try:
        asyncio.run(layer_builder.start_scheduler())
    except KeyboardInterrupt:
        logger.info("Scheduler gracefully stopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()