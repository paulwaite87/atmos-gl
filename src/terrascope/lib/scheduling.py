#!/usr/bin/env python3
"""Shared scheduling primitive for the long-running services (architecture review
candidate "one long-running-service scaffold"). Housekeeper.run() and
CollectorService.run() each hand-rolled the identical "has this interval elapsed
since the last run" check; a single shared scaffold across all three services isn't
justified (Housekeeper is a standalone sync process with no concurrency need, while
CollectorService/LayerBuilder are async because they genuinely run other things
concurrently -- forcing Housekeeper into async would be cosmetic, not functional; and
even the two async loops differ in real structure, e.g. CollectorService's second,
independently-gated backfill-drain action that LayerBuilder has no equivalent of).
This helper is the one piece that actually was duplicated verbatim.

Deliberately clock-agnostic: `now` is a parameter, not computed here, because
Housekeeper uses time.time() (wall clock) and CollectorService uses
asyncio.get_event_loop().time() (monotonic, event-loop-relative) -- the two aren't
interchangeable, so each caller passes its own clock's reading in.

Validated with ast.parse.
"""


def interval_elapsed(last_run, now: float, interval_s: float) -> bool:
    """True if `interval_s` seconds have passed since `last_run`, or if the service
    has never run yet (last_run is None) -- the latter case means "run immediately
    the first time" rather than waiting a full interval before ever doing anything."""
    return last_run is None or (now - last_run) >= interval_s
