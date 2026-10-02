#!/usr/bin/env python3
"""Per-collector mutual exclusion across processes: a Postgres session-level advisory
lock keyed on the collector's section, held for the duration of one run.

Needed because a collector can now be run by hand (collectors/run_once.py, `make
collect`) from a second process while CollectorService's own sequential sweep might
reach the same collector -- two concurrent runs of a file-cache collector would race
writing the same cache file. The scheduled driver takes the lock non-blocking and
skips that collector for the cycle if a manual run holds it; a manual run waits for
an in-progress scheduled run to finish.
"""
from contextlib import contextmanager

from sqlalchemy import text

from atmos_gl.db.engine import engine as _default_engine


@contextmanager
def collector_lock(name: str, wait: bool = False, on_wait=None, bind=None):
    """Yields True if the lock for collector `name` is held for the with-block,
    False if it was busy and `wait` is False. With `wait`, `on_wait()` (if given) is
    called once before blocking, so a caller can say what it's waiting for."""
    key = f"collector:{name}"
    with (bind or _default_engine).connect() as conn:
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key}
        ).scalar()
        if not acquired and wait:
            if on_wait:
                on_wait()
            conn.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": key})
            acquired = True
        try:
            yield bool(acquired)
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
