#!/usr/bin/env python3
"""Base class for all event-feed collectors.

Provides per-collector scheduling (runs_per_day → period_s, is_stale) and a cheap
remote-freshness hook (has_new_data, defaulting to True) that subclasses override with
HEAD/ETag checks where the source supports them. The actual data fetch is collect().

The scheduling contract used by collect_event_feeds():
  - is_stale(last_run)   True  → enough time has elapsed; worth checking remotely
  - has_new_data()       True  → remote data changed; call collect()
                         False → unchanged; skip collect() but still update last_run
  - collect()            perform the full fetch + DB upsert

ETag/Last-Modified state is stored in a class-level dict keyed by URL so it persists
across the per-cycle instance recreation inside collect_event_feeds(), without needing
to thread state through the caller. The dict is process-scoped, which is correct: the
data_collector service (CollectorService) runs as a single long-running process.

data_status() (on both CollectorBase and AsyncCollectorBase below) is read-only: it
reports the process_status row written by the orchestration layer (collectors/__init__.py
_drive(), the async collectors' own run() loops), it never writes one itself. That split
matters because data_status() must also be callable from a process that never runs
collection at all (map_api, serving the Config UI's Data Status tab) — constructing a
throwaway collector instance there is cheap and side-effect-free, exactly like _drive()
already does for collect().
"""

import time
import logging

from atmos_gl.lib.data_status import (
    freshness_percent,
    estimate_next_update,
    period_s_from_runs_per_day,
    read_process_status,
    build_status,
)

logger = logging.getLogger(__name__)


class CollectorBase:
    """Abstract base for a stateless, schedulable event-feed collector.

    Subclasses must set `section` (the config section name, also used as the key in
    last_runs) and implement `collect()`. Optionally override `has_new_data()` for a
    cheap HEAD/ETag pre-check.
    """

    section: str = ""  # override in every subclass

    # Key into data_collector.channel_enabled -- the per-source data-acquisition
    # opt-out, independent of any layer's frontend `enabled`. None (default) means this
    # collector isn't gated by it at all (e.g. markers -- reads a local file, not a
    # remote source, so there's no "good citizen" opt-out to offer). Usually equals
    # `section`, but set explicitly where it doesn't (e.g. SatellitesCollector.section
    # == "satellites_collector" but its channel is "satellites") -- see _drive() in
    # collectors/__init__.py.
    channel_key: str | None = None

    # Key into data_collector.datasources -- see source_url() below. "" (default) means
    # this collector has no single browsable remote URL (e.g. markers, which syncs a
    # local file), so source_url() returns None and the Data Status page renders a plain,
    # non-clickable label.
    datasource_key: str = ""

    # Process-level ETag/Last-Modified cache: url -> last-seen marker string.
    # Shared across all collector subclasses (keyed by URL, so no collision).
    _etag_cache: dict[str, str] = {}

    def __init__(self, config):
        from atmos_gl.db.process_status_adapter import ProcessStatusAdapter

        self.config = config
        self.process_status_adapter = ProcessStatusAdapter()
        self.settings = config.get_section(self.section) or {}
        # Mirrors AsyncCollectorBase.refresh_settings() -- a fresh instance is
        # constructed every _drive() cycle (collectors/__init__.py), so applying this
        # in __init__ has the same live-update effect as that class's periodic refresh.
        lvl = self.settings.get("log_level")
        if lvl:
            from atmos_gl.lib.logging import set_loglevel

            set_loglevel(lvl)

    def datasource_url(self, key: str) -> str:
        """The configured data_collector.datasources[key] base URL, or "" if unset.

        Every collector's actual source URL lives in this one shared dict now (mirrors
        FieldCollectorBase.base_url(), generalised to sources whose `section` isn't
        "data_collector" -- they still need their own section for `enabled` etc, so this
        reaches into data_collector separately instead of overriding `section`)."""
        datasources = self.config.get_setting("data_collector", "datasources", {}) or {}
        return (datasources.get(key) or "").rstrip("/")

    def source_url(self) -> str | None:
        """The external URL this collector fetches from, for the Data Status page's
        clickable-label link -- None if there's no single browsable URL (datasource_key
        unset, e.g. markers) or none is configured. Override where the URL doesn't live
        in data_collector.datasources at all (see StormsCollector, which keeps its two
        ATCF mirror URLs in its own section)."""
        if not self.datasource_key:
            return None
        return self.datasource_url(self.datasource_key) or None

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    @property
    def workdir(self) -> str:
        """Process workdir (common.workdir). File-cache collectors (sst, clouds) write
        their cache under {workdir}/data; pure-DB event feeds ignore it. Kept on the base
        so every collector resolves the workdir the same way."""
        return self.config.get_setting("common", "workdir", ".")

    @property
    def period_s(self) -> float:
        """Seconds between runs, derived from the runs_per_day config key."""
        return period_s_from_runs_per_day(self.settings.get("runs_per_day", 1))

    def is_stale(self, last_run: float | None) -> bool:
        """True when enough monotonic time has passed to warrant another check.

        last_run is a time.monotonic() value recorded by collect_event_feeds after the
        previous check (whether or not data was actually fetched). None on first run.
        """
        if last_run is None:
            return True
        return (time.monotonic() - last_run) >= self.period_s

    # ------------------------------------------------------------------
    # Remote-freshness hook
    # ------------------------------------------------------------------

    def has_new_data(self) -> bool:
        """Cheap remote check before a full collect().

        Returns True  → remote data has changed (or we can't tell); call collect().
        Returns False → unchanged; skip collect() this cycle.

        Default: always True (unconditional collect). Subclasses override with a HEAD
        request + ETag/Last-Modified comparison where the source supports it. Must be
        safe to call unconditionally — on any network error it should return True so
        we fall through to collect() rather than silently dropping an update.
        """
        return True

    # ------------------------------------------------------------------
    # Data fetch
    # ------------------------------------------------------------------

    def collect(self) -> None:
        """Perform the full fetch and DB upsert. Override in every subclass."""
        raise NotImplementedError(f"{type(self).__name__}.collect() not implemented")

    # ------------------------------------------------------------------
    # Data Status (read-only; see module docstring)
    # ------------------------------------------------------------------

    def data_status(self) -> dict:
        """Snapshot for the Config UI's Data Status tab: a decaying-freshness `percent`
        (100 right after a successful run, decaying to 0 as it becomes overdue past
        period_s), `last_updated`, `next_update`, `enabled`, and an optional error
        `detail`. Read straight from process_status (written by _drive()); this method
        never writes. FieldCollectorBase overrides this with a coverage-based percent
        instead, since "how much of the forecast window is fetched" is more meaningful
        for those than a freshness decay.

        `self.enabled` here is the layer's frontend-visibility flag, not a collection
        kill-switch — _drive() runs every COLLECTORS/CACHE_COLLECTORS entry unconditionally
        of it (see collectors/__init__.py). next_update must reflect that real, unconditional
        schedule rather than reporting "disabled" for a source that is in fact still being
        collected in the background."""
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.section
        )
        return build_status(
            name=self.section,
            kind="collector",
            percent=freshness_percent(last_updated, self.period_s),
            last_updated=last_updated,
            next_update=estimate_next_update(last_updated, self.period_s, True),
            enabled=self.enabled,
            detail=last_error,
            status=status,
        )

    # ------------------------------------------------------------------
    # Shared HEAD helper
    # ------------------------------------------------------------------

    @classmethod
    def _head_changed(cls, url: str, timeout: int = 8) -> bool | None:
        """Issue a HEAD request and compare ETag/Last-Modified against the cache.

        Returns:
          True   — marker changed (or absent) → data may have changed
          False  — marker unchanged → skip this cycle
          None   — request failed → caller should default to True (safe)

        Updates _etag_cache[url] on change.
        """
        import urllib.request
        import urllib.error

        try:
            req = urllib.request.Request(
                url,
                method="HEAD",
                headers={"User-Agent": "AtmosGL-Collector/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                marker = r.headers.get("ETag") or r.headers.get("Last-Modified")
        except Exception as exc:
            logger.debug(f"HEAD {url!r} failed: {exc!r}")
            return None

        if not marker:
            return True  # server gives no freshness signal → assume changed

        cached = cls._etag_cache.get(url)
        if cached == marker:
            return False  # unchanged
        cls._etag_cache[url] = marker
        return True  # new or changed marker → proceed

    @classmethod
    def _head_changed_or_default(cls, url: str, label: str) -> bool:
        """The single-URL has_new_data() wrapper hand-duplicated across quakes.py,
        volcanoes.py, and satellites.py: a failed HEAD probe (_head_changed returns
        None) defaults to True (collect anyway, safe fallback), and an unchanged
        remote logs a debug line using `label` before returning False.

        storms.py is NOT a caller of this: it HEADs two ATCF mirror URLs and logs one
        combined "unchanged" message after checking both, not one message per URL, so
        it keeps its own loop rather than being forced through this per-URL shape.
        """
        result = cls._head_changed(url)
        if result is None:
            return True
        if not result:
            logger.debug(f"{label}: remote unchanged; skipping collect.")
        return result


class AsyncCollectorBase:
    """Base for long-running async collectors (shipping, lightning).

    These manage their own config/db lifecycle and run as persistent asyncio coroutines
    with their own sleep/retry cadence. Unlike CollectorBase (sync, periodic), they are
    not driven by collect_event_feeds() — they self-schedule via await asyncio.sleep().

    They run as separate Docker services for now because the synchronous GFS downloads
    in DataCollector.collect_once() would starve their event loops if merged into one
    process. Consolidating them into data_collector is a follow-on step that requires
    making the GFS/RTOFS downloads async (asyncio.to_thread + thread-safe DB handles).

    The common interface here lets them live in one package, share logging conventions,
    and be invoked via a standard main() entry point.
    """

    section: str = ""
    # Always None -- shipping/lightning are NOT part of data_collector.channel_enabled,
    # they keep their own real enabled kill-switch instead (see CollectorBase.channel_key
    # for the full contract; this exists so routes/status.py can read
    # CollectorCls.channel_key uniformly across every registry without a type check).
    channel_key: str | None = None
    # Expected seconds between successful heartbeats (see data_status()). No is_stale/
    # period_s equivalent exists for these (they self-schedule inside run()), so each
    # subclass estimates its own from its own settings/cadence. Default is a placeholder;
    # ShippingCollector/LightningCollector override with a real estimate.
    heartbeat_period_s: float = 300.0

    # See CollectorBase.datasource_key -- same contract, duplicated here since
    # AsyncCollectorBase is a sibling hierarchy, not a subclass.
    datasource_key: str = ""

    def __init__(self, config_path: str):
        from atmos_gl.lib.config import AtmosGLConfig
        from atmos_gl.db.process_status_adapter import ProcessStatusAdapter

        self.config_path = config_path
        self.config = AtmosGLConfig(config_path)
        self.process_status_adapter = ProcessStatusAdapter()
        self.settings: dict = {}
        self.refresh_settings()

    def refresh_settings(self) -> None:
        self.config.load()
        self.settings = self.config.get_section(self.section) or {}
        from atmos_gl.lib.logging import set_loglevel
        lvl = self.settings.get("log_level")
        if lvl:
            set_loglevel(lvl)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    def datasource_url(self, key: str) -> str:
        """The configured data_collector.datasources[key] base URL, or "" if unset.
        See CollectorBase.datasource_url() -- same contract, duplicated here since
        AsyncCollectorBase is a sibling hierarchy, not a subclass."""
        datasources = self.config.get_setting("data_collector", "datasources", {}) or {}
        return (datasources.get(key) or "").rstrip("/")

    def source_url(self) -> str | None:
        """See CollectorBase.source_url() -- same contract, duplicated here since
        AsyncCollectorBase is a sibling hierarchy, not a subclass."""
        if not self.datasource_key:
            return None
        return self.datasource_url(self.datasource_key) or None

    async def run(self) -> None:
        raise NotImplementedError(f"{type(self).__name__}.run() not implemented")

    def data_status(self) -> dict:
        """Same decaying-freshness snapshot as CollectorBase.data_status(), using
        heartbeat_period_s in place of period_s (these have no is_stale cadence — they
        self-schedule inside run() and record a heartbeat at their own natural
        checkpoint, e.g. once per rotation/scan)."""
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.section
        )
        return build_status(
            name=self.section,
            kind="collector",
            percent=freshness_percent(last_updated, self.heartbeat_period_s),
            last_updated=last_updated,
            next_update=estimate_next_update(
                last_updated, self.heartbeat_period_s, self.enabled
            ),
            enabled=self.enabled,
            detail=last_error,
            status=status,
        )

    @classmethod
    def main(cls) -> None:
        """Standard entry point for standalone / Docker service mode."""
        import argparse
        import asyncio
        from atmos_gl.lib.logging import setup_logging

        setup_logging()
        parser = argparse.ArgumentParser(description=cls.__name__)
        parser.add_argument("--config", required=True)
        args = parser.parse_args()
        asyncio.run(cls(args.config).run())
