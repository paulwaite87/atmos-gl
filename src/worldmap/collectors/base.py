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
DataCollector is a single long-running process.
"""

import time
import logging

logger = logging.getLogger(__name__)


class CollectorBase:
    """Abstract base for a stateless, schedulable event-feed collector.

    Subclasses must set `section` (the config section name, also used as the key in
    last_runs) and implement `collect()`. Optionally override `has_new_data()` for a
    cheap HEAD/ETag pre-check.
    """

    section: str = ""  # override in every subclass

    # Process-level ETag/Last-Modified cache: url -> last-seen marker string.
    # Shared across all collector subclasses (keyed by URL, so no collision).
    _etag_cache: dict[str, str] = {}

    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.settings = config.get_section(self.section) or {}

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    @property
    def period_s(self) -> float:
        """Seconds between runs, derived from the runs_per_day config key."""
        rpd = float(self.settings.get("runs_per_day", 1))
        return 86400.0 / max(rpd, 0.01)

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
                headers={"User-Agent": "WorldMap-Collector/1.0"},
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

    def __init__(self, config_path: str):
        from worldmap.lib.config import WorldMapConfig
        from worldmap.lib.db import Database

        self.config_path = config_path
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.settings: dict = {}
        self.refresh_settings()

    def refresh_settings(self) -> None:
        self.config.load()
        self.settings = self.config.get_section(self.section) or {}
        from worldmap.lib.logging import set_loglevel
        lvl = self.settings.get("log_level")
        if lvl:
            set_loglevel(lvl)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    async def run(self) -> None:
        raise NotImplementedError(f"{type(self).__name__}.run() not implemented")

    @classmethod
    def main(cls) -> None:
        """Standard entry point for standalone / Docker service mode."""
        import argparse
        import asyncio
        from worldmap.lib.logging import setup_logging

        setup_logging()
        parser = argparse.ArgumentParser(description=cls.__name__)
        parser.add_argument("--config", required=True)
        args = parser.parse_args()
        asyncio.run(cls(args.config).run())
