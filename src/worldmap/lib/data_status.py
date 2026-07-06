#!/usr/bin/env python3
"""Shared building blocks for the Config UI's Data Status tab (architecture review
candidate "one status module"). CollectorBase.data_status(), AsyncCollectorBase.
data_status(), FieldCollectorBase.data_status() and Updater.layer_status() each built
the same final {name, kind, percent, last_updated, next_update, enabled, detail} dict
by hand, and tasks/common.py imported two of these formulas across a package boundary
purely for lack of a neutral home. Lives in lib/ alongside the other cross-cutting,
no-single-domain-owner modules (config, logging, fieldstore).

What's deliberately NOT here: the coverage-based percent calculations in
FieldCollectorBase (product x forecast-hour coverage) and Updater.layer_status()
(render-completion coverage) stay local to their callers -- they're genuinely
different domain math, not the same formula duplicated.

Validated with ast.parse.
"""
from datetime import datetime, timedelta, timezone


def freshness_percent(last_updated, period_s: float) -> float:
    """Shared decay formula for single-shot/continuous collectors: 100% right after a
    successful run/check, decaying linearly to 0% by the time we're a full extra
    period_s overdue past the expected next run. Deliberately not a flat binary — a
    collector that's overdue (crashed, backend down, etc.) should visibly decay on the
    Data Status bar rather than sit at a permanent 100%."""
    if last_updated is None:
        return 0.0
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    overdue = (now - last_updated).total_seconds() - period_s
    if overdue <= 0:
        return 100.0
    return max(0.0, 100.0 * (1 - overdue / period_s))


def estimate_next_update(last_updated, period_s: float, enabled: bool):
    """next_update for the Data Status UI. Three cases:
      * disabled     -> None (it won't run again until re-enabled; showing a guessed time
                         here would be actively misleading, not just imprecise)
      * never run yet (last_updated is None) but enabled -> now + period_s, an estimate
        (we don't know exactly when this cycle started, only that it's due within one
        period) rather than leaving the UI with nothing at all for a collector that just
        hasn't completed its first cycle
      * has run before -> last_updated + period_s, the precise scheduled next run
    """
    if not enabled:
        return None
    if last_updated is None:
        return datetime.now(timezone.utc) + timedelta(seconds=period_s)
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=timezone.utc)
    return last_updated + timedelta(seconds=period_s)


def period_s_from_runs_per_day(runs_per_day) -> float:
    """Seconds between runs, derived from a runs_per_day config value. Shared by
    CollectorBase.period_s and Updater.layer_status()'s single-shot branch, which
    computed this identically."""
    rpd = float(runs_per_day or 1)
    return 86400.0 / max(rpd, 0.01)


def read_process_status(process_status_adapter, name: str):
    """(last_updated, last_error) for `name`'s most recent process_status row, or
    (None, None) if it has none yet. The same read every data_status()/layer_status()
    implementation starts with."""
    row = process_status_adapter.get_process_status(name)
    if not row:
        return None, None
    return row["last_updated"], row["last_error"]


def build_status(
    *, name: str, kind: str, percent: float, last_updated, next_update, enabled: bool, detail
) -> dict:
    """Assembles the final Data Status dict shape every data_status()/layer_status()
    implementation returns. `percent` is rounded here so callers pass the raw computed
    value rather than each remembering `round(percent, 1)` themselves."""
    return {
        "name": name,
        "kind": kind,
        "percent": round(percent, 1),
        "last_updated": last_updated,
        "next_update": next_update,
        "enabled": enabled,
        "detail": detail,
    }
