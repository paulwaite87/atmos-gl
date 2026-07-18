#!/usr/bin/env python3
"""Shared building blocks for the Config UI's Data Status tab (architecture review
candidate "one status module"). CollectorBase.data_status(), AsyncCollectorBase.
data_status(), FieldCollectorBase.data_status() and Updater.layer_status() each built
the same final {name, kind, percent, last_updated, next_update, enabled, detail} dict
by hand, and tasks/common.py imported two of these formulas across a package boundary
purely for lack of a neutral home. Lives in lib/ alongside the other cross-cutting,
no-single-domain-owner modules (config, logging, fieldstore).

freshness_data_status() (architecture review candidate "one shared URL + freshness
contract") is the further-consolidated body of CollectorBase.data_status() and
AsyncCollectorBase.data_status() -- those two classes are siblings, not a subclass
relationship (one sync/periodic, one async/self-scheduling), so they can't share this
via inheritance; the one real behavioural difference between them (whether next_update
respects the `enabled` flag) is an explicit parameter here rather than a buried literal
hand-copied across two call sites 150 lines apart. resolve_datasource_url()/
resolve_source_url() are the same two classes' other duplicated pair, consolidated the
same way.

What's deliberately NOT here: the coverage-based percent calculations in
FieldCollectorBase (product x forecast-hour coverage) and Updater.layer_status()
(render-completion coverage) stay local to their callers -- they're genuinely
different domain math, not the same formula duplicated.

resolve_run_epoch_utc() is a third consolidation, unrelated to the data_status-dict
formulas above: routes/config.py's scrubber timeline, FieldCollectorBase's coverage
math, and Updater.layer_status()'s now-onward filtering each independently computed a
run's f000 valid-time from its (run_date, run_id) catalog columns, with two of the
three call sites unable to handle a date/DATE column instead of a string. Consolidated
into one tolerant implementation here since it's the same date-math sitting alongside
the same status-page building blocks, not layer/collector-specific.

Validated with ast.parse.
"""
from datetime import date, datetime, timedelta, timezone


def resolve_run_epoch_utc(run_date, run_id) -> datetime:
    """Build a run's f000 valid-time (UTC) from run_date (a date/datetime, or a string
    in "YYYYMMDD" or "YYYY-MM-DD" form) and run_id (str/int hour). Tolerant of the
    catalog column being either text or DATE."""
    run_hour = int(run_id)

    if isinstance(run_date, datetime):
        d = run_date.date()
    elif isinstance(run_date, date):
        d = run_date
    else:
        s = str(run_date).strip()
        if "-" in s:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        else:
            d = datetime.strptime(s, "%Y%m%d").date()

    return datetime(d.year, d.month, d.day, run_hour, 0, 0, tzinfo=timezone.utc)


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


# The only runs_per_day values selectable from the Data Status page's per-collector
# widget (routes/status.py's set_runs_per_day endpoint) -- 96 = every 15 minutes,
# matching data_collector's historical update_minutes default.
RUNS_PER_DAY_CHOICES = (1, 4, 6, 12, 24, 48, 96)


def read_process_status(process_status_adapter, name: str):
    """(last_updated, last_error, status) for `name`'s most recent process_status row,
    or (None, None, None) if it has none yet. The same read every data_status()/
    layer_status() implementation starts with. `status` is "idle"/"running"/"success"/
    "failed" -- set by record_process_start()/record_process_run()
    (db/process_status_adapter.py); "running" is the one value that can't be inferred
    from last_updated/last_error alone, since work in flight touches neither."""
    row = process_status_adapter.get_process_status(name)
    if not row:
        return None, None, None
    return row["last_updated"], row["last_error"], row.get("status")


def build_status(
    *,
    name: str,
    kind: str,
    percent: float,
    last_updated,
    next_update,
    enabled: bool,
    detail,
    status: str | None = None,
) -> dict:
    """Assembles the final Data Status dict shape every data_status()/layer_status()
    implementation returns. `percent` is rounded here so callers pass the raw computed
    value rather than each remembering `round(percent, 1)` themselves. `status` is
    optional -- callers that don't track a "running" state (nothing calls
    record_process_start() for them yet) simply omit it and the UI treats null the
    same as before this field existed."""
    return {
        "name": name,
        "kind": kind,
        "percent": round(percent, 1),
        "last_updated": last_updated,
        "next_update": next_update,
        "enabled": enabled,
        "detail": detail,
        "status": status,
    }


def resolve_datasource_url(config, key: str) -> str:
    """The configured data_collector.datasources[key] base URL, or "" if unset.

    Every collector's actual source URL lives in this one shared config dict --
    CollectorBase.datasource_url()/AsyncCollectorBase.datasource_url() are thin
    delegating wrappers around this (kept on each class since many concrete collectors
    call self.datasource_url(key) directly, with an explicit key not necessarily equal
    to self.datasource_key)."""
    datasources = config.get_setting("data_collector", "datasources", {}) or {}
    return (datasources.get(key) or "").rstrip("/")


def resolve_source_url(config, datasource_key: str) -> str | None:
    """The collector's upstream URL for the Data Status page's clickable-label link --
    None if datasource_key is unset (no single browsable URL, e.g. markers, which syncs
    a local file) or the key has no configured value. Collectors whose URL doesn't live
    in data_collector.datasources at all (see StormsCollector, which keeps its two ATCF
    mirror URLs in its own section) override source_url() instead of using this."""
    if not datasource_key:
        return None
    return resolve_datasource_url(config, datasource_key) or None


def freshness_data_status(
    process_status_adapter,
    section: str,
    period_s: float,
    enabled: bool,
    *,
    next_update_respects_enabled: bool = True,
) -> dict:
    """Shared body of CollectorBase.data_status()/AsyncCollectorBase.data_status():
    read process_status, compute the decaying freshness percent + next_update, assemble
    via build_status().

    next_update_respects_enabled is the one real behavioural divergence between the two
    callers -- everything else here is identical:
      * CollectorBase (default False) -- collection is unconditional of the frontend
        `enabled` flag (_drive() runs every collector regardless of it), so next_update
        must reflect the real, unconditional schedule rather than reporting "disabled"
        for a source that's still being collected in the background.
      * AsyncCollectorBase (pass True) -- shipping/lightning's `enabled` IS a real
        kill-switch, so next_update should correctly report None while disabled.
    """
    last_updated, last_error, status = read_process_status(process_status_adapter, section)
    return build_status(
        name=section,
        kind="collector",
        percent=freshness_percent(last_updated, period_s),
        last_updated=last_updated,
        next_update=estimate_next_update(
            last_updated, period_s, enabled if next_update_respects_enabled else True
        ),
        enabled=enabled,
        detail=last_error,
        status=status,
    )
