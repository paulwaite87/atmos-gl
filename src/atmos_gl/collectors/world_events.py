#!/usr/bin/env python3
"""GDELT Event Database 2.0 -> database ("World Events" layer -- see CONTEXT.md).
Real-time export files, tab-delimited, no header, 61 fields (see _COL_* below, field
positions verified against a live export file rather than assumed from GDELT's own
published codebook alone, which is easy to miscount from: EventBaseCode and the two
Actor*Geo_ADM2Code fields sit between more commonly-cited field names).

Filtered to a curated, high-signal CAMEO code allowlist, not broad root bands --
GDELT logs on the order of hundreds of thousands of events/day across 300+ codes,
nowhere near all of it is a "world event" in the sense this layer means. Four
categories:
  - Explosion: 183, 1831-1833 (suicide/car/roadside bombing)
  - Warfare: 190-196 (conventional force through ceasefire violation)
  - Targeted/mass violence: 181, 185, 186, 200-204, 2041, 2042
  - Diplomacy: root 04 (040-046) AND an Actor1Name/Actor2Name match against a curated
    organization list -- root 04 alone is too generic (any two officials on a routine
    call would qualify); see _DIPLOMACY_ORGS.

Explosion/Warfare/Targeted-violence additionally require at least one actor to resolve
to a real state/military/organized-group entity (a non-blank Actor1/Actor2 CountryCode
or Type1-3Code -- see _has_state_actor()), the same actor-vetting principle Diplomacy
already applies via _DIPLOMACY_ORGS. GDELT's NLP event-coder matches on bare verb
phrases and will code figurative "battle"/"fight"/"war" language -- e.g. a film-casting
article about "the battle to become the next James Bond" -- into these conflict bands;
such an article's actors don't resolve to anything in CAMEO's country/military/group
dictionaries the way a real conflict's actors do, so requiring that resolution filters
the false positive at the source instead of guessing at tone/mention thresholds.

Coverage is tracked per export FILE, not inferred from stored events: GDELT publishes
one export file per 15-minute slot (verified live: :00/:15/:30/:45 exist, an off-grid
timestamp 404s), and every processed slot gets a world_event_export_files row. A cycle
reads lastupdate.txt for the newest published slot, enumerates every slot back to
now - backfill_days, and fetches whichever have no row -- newest first, capped at
_MAX_FILES_PER_CYCLE so a long outage's gap can't monopolise the shared collector loop.
So a gap anywhere in the window (not just at its old end) self-heals over the next few
cycles, a file yielding zero curated events still counts as covered, and the export
URL is computed from the slot directly -- no scan of the 128 MB masterfilelist.txt. A
404 slot is recorded "missing" and not retried; any other failure leaves the slot
unrecorded, so it's retried next cycle.

After ingesting, each cycle also fetches a headline + summary for source articles that
don't have one yet (lib/article_preview.py -- the publisher's og:/twitter:/<meta>
tags), newest events first, at most _MAX_PREVIEWS_PER_CYCLE URLs across
_PREVIEW_WORKERS threads. Keyed by URL, since GDELT often codes one article into
several events. ~760 distinct URLs/day at ~2 s each (measured) is ~8 per 15-min cycle
in steady state; the cap only bites while a backlog drains. has_new_data() is "any
slot or article preview pending".
"""
import io
import logging
import re
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.world_event_adapter import WorldEventAdapter
from atmos_gl.lib.article_preview import fetch_article_preview

logger = logging.getLogger(__name__)

# 0-indexed column positions in GDELT 2.0's tab-delimited export CSV.
_COL_GLOBALEVENTID = 0
_COL_ACTOR1NAME = 6
_COL_ACTOR1COUNTRYCODE = 7
_COL_ACTOR1TYPE1CODE = 12
_COL_ACTOR1TYPE2CODE = 13
_COL_ACTOR1TYPE3CODE = 14
_COL_ACTOR2NAME = 16
_COL_ACTOR2COUNTRYCODE = 17
_COL_ACTOR2TYPE1CODE = 22
_COL_ACTOR2TYPE2CODE = 23
_COL_ACTOR2TYPE3CODE = 24
_COL_EVENTCODE = 26
_COL_GOLDSTEIN = 30
_COL_NUM_MENTIONS = 31
_COL_NUM_SOURCES = 32
_COL_AVG_TONE = 34
_COL_ACTIONGEO_FULLNAME = 52
_COL_ACTIONGEO_LAT = 56
_COL_ACTIONGEO_LONG = 57
_COL_DATEADDED = 59
_COL_SOURCEURL = 60
_MIN_COLS = 61

_EXPLOSION_CODES = {"183", "1831", "1832", "1833"}
_WARFARE_CODES = {"190", "191", "192", "193", "194", "195", "196"}
_TARGETED_VIOLENCE_CODES = {
    "181", "185", "186", "200", "201", "202", "203", "204", "2041", "2042",
}
_DIPLOMACY_EVENT_CODES = {"040", "041", "042", "043", "044", "045", "046"}

# Curated, not exhaustive -- high-profile multinational/summit bodies only, so a
# root-04 event between two ordinary officials doesn't qualify as a World Event (see
# module docstring). Matched case-insensitively as a substring against Actor1Name/
# Actor2Name, which GDELT already resolves to plain organization-name text.
_DIPLOMACY_ORGS = (
    "NATO", "UNITED NATIONS", "G7", "G8", "G20", "EUROPEAN UNION", "ASEAN", "OPEC",
    "AFRICAN UNION", "ARAB LEAGUE", "WORLD ECONOMIC FORUM",
)

_EXPORT_FILE_RE = re.compile(r"(\d{14})\.export\.CSV\.zip$")
_SLOT = timedelta(minutes=15)
# 48 files = 12 hours of GDELT per cycle (~65 KB each, a few seconds per file at most).
_MAX_FILES_PER_CYCLE = 48
# Article previews: network-bound, so threaded; 120 URLs / 8 workers at ~2 s each
# (up to 10 s timeout) keeps a backlog-draining cycle to roughly 30-150 s.
_MAX_PREVIEWS_PER_CYCLE = 120
_PREVIEW_WORKERS = 8


class ExportFileMissing(Exception):
    """GDELT returned 404 for an export slot -- distinct from a transient failure,
    since a genuinely absent file should be recorded and never re-requested."""


def _has_state_actor(*codes: str | None) -> bool:
    """True when at least one Actor1/Actor2 CountryCode or Type1-3Code field resolved
    to something. GDELT's CAMEO actor dictionary only populates these when the raw
    actor text matched a known country, government, military, or organized-group
    pattern -- an unresolvable proper noun (e.g. a film-casting article's "Pierce
    Brosnan") leaves them all blank even though Actor1Name/Actor2Name still carry the
    raw text. See module docstring for why this gates the conflict categories."""
    return any((c or "").strip() for c in codes)


def _classify(
    event_code: str,
    actor1_name: str | None,
    actor2_name: str | None,
    has_state_actor: bool = False,
) -> str | None:
    if event_code in _EXPLOSION_CODES:
        return "explosion" if has_state_actor else None
    if event_code in _WARFARE_CODES:
        return "warfare" if has_state_actor else None
    if event_code in _TARGETED_VIOLENCE_CODES:
        return "targeted_violence" if has_state_actor else None
    if event_code in _DIPLOMACY_EVENT_CODES:
        names = f"{actor1_name or ''} {actor2_name or ''}".upper()
        if any(org in names for org in _DIPLOMACY_ORGS):
            return "diplomacy"
    return None


def _parse_export_rows(csv_text: str) -> list[dict]:
    """Parses one GDELT export CSV's text into row dicts ready for
    WorldEventAdapter.upsert_events(), pre-filtered to the curated category allowlist
    (see module docstring). Deliberately does NOT apply the min_mentions floor --
    that's a settings-tunable threshold the caller applies, not baked into parsing."""
    rows = []
    for line in csv_text.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < _MIN_COLS:
            continue

        event_code = parts[_COL_EVENTCODE].strip()
        actor1_name = parts[_COL_ACTOR1NAME].strip() or None
        actor2_name = parts[_COL_ACTOR2NAME].strip() or None
        has_state_actor = _has_state_actor(
            parts[_COL_ACTOR1COUNTRYCODE], parts[_COL_ACTOR1TYPE1CODE],
            parts[_COL_ACTOR1TYPE2CODE], parts[_COL_ACTOR1TYPE3CODE],
            parts[_COL_ACTOR2COUNTRYCODE], parts[_COL_ACTOR2TYPE1CODE],
            parts[_COL_ACTOR2TYPE2CODE], parts[_COL_ACTOR2TYPE3CODE],
        )
        category = _classify(event_code, actor1_name, actor2_name, has_state_actor)
        if category is None:
            continue

        lat_str = parts[_COL_ACTIONGEO_LAT].strip()
        lon_str = parts[_COL_ACTIONGEO_LONG].strip()
        if not lat_str or not lon_str:
            continue

        try:
            lat, lon = float(lat_str), float(lon_str)
            num_mentions = int(parts[_COL_NUM_MENTIONS].strip() or 0)
            num_sources = int(parts[_COL_NUM_SOURCES].strip() or 0)
            goldstein_raw = parts[_COL_GOLDSTEIN].strip()
            goldstein = float(goldstein_raw) if goldstein_raw else None
            tone_raw = parts[_COL_AVG_TONE].strip()
            avg_tone = float(tone_raw) if tone_raw else None
            event_date = datetime.strptime(
                parts[_COL_DATEADDED].strip(), "%Y%m%d%H%M%S"
            ).replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue

        rows.append(
            {
                "id": parts[_COL_GLOBALEVENTID].strip(),
                "category": category,
                "event_code": event_code,
                "actor1_name": actor1_name,
                "actor2_name": actor2_name,
                "action_geo_full_name": parts[_COL_ACTIONGEO_FULLNAME].strip() or None,
                "lat": lat,
                "lon": lon,
                "event_date": event_date.isoformat(),
                "num_mentions": num_mentions,
                "num_sources": num_sources,
                "goldstein_scale": goldstein,
                "avg_tone": avg_tone,
                "source_url": parts[_COL_SOURCEURL].strip() or None,
            }
        )
    return rows


def _fetch_export_csv(url: str) -> str:
    """Downloads one GDELT export.CSV.zip and returns its single member's decoded
    text. GDELT's export files are latin-1 (confirmed against a live file: several
    actor/place names carry raw high-byte characters that aren't valid UTF-8).

    Raises ExportFileMissing on a 404 and requests.RequestException on any other
    failure. Uses requests directly rather than CollectorBase._get(), which collapses
    every failure into None -- the 404-vs-transient distinction is the whole point
    here (see the module docstring)."""
    r = requests.get(url, timeout=30, headers={"User-Agent": "AtmosGL-Collector/1.0"})
    if r.status_code == 404:
        raise ExportFileMissing(url)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        name = zf.namelist()[0]
        return zf.read(name).decode("latin-1")


def _export_url_from_lastupdate(text: str) -> str | None:
    for line in text.splitlines():
        if ".export.CSV.zip" in line:
            parts = line.split()
            if parts:
                return parts[-1]
    return None


def _slot_from_export_url(url: str) -> datetime | None:
    m = _EXPORT_FILE_RE.search(url)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _floor_to_slot(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


def _expected_slots(window_start: datetime, latest: datetime) -> list[datetime]:
    """Every 15-minute slot from window_start (floored onto the grid) through latest
    inclusive, newest first."""
    slots = []
    slot = latest
    start = _floor_to_slot(window_start)
    while slot >= start:
        slots.append(slot)
        slot -= _SLOT
    return slots


class WorldEventsCollector(CollectorBase):
    section = "world_events"
    channel_key = "world_events"
    datasource_key = "world_events"

    def __init__(self, config):
        super().__init__(config)
        self.world_event_adapter = WorldEventAdapter()

    def _lastupdate_url(self) -> str:
        return f"{self.datasource_url('world_events').rstrip('/')}/lastupdate.txt"

    def _export_url(self, slot: datetime) -> str:
        base = self.datasource_url("world_events").rstrip("/")
        return f"{base}/{slot.strftime('%Y%m%d%H%M%S')}.export.CSV.zip"

    def _window_start(self) -> datetime:
        backfill_days = int(self.settings.get("backfill_days", 3))
        return datetime.now(timezone.utc) - timedelta(days=backfill_days)

    def _latest_slot(self) -> datetime | None:
        """The newest published slot, per lastupdate.txt -- the upper bound on what can
        be fetched (a slot past it may not exist yet). None if it can't be read."""
        r = self._get(self._lastupdate_url(), timeout=10)
        if r is None:
            return None
        url = _export_url_from_lastupdate(r.text)
        return _slot_from_export_url(url) if url else None

    def _pending_slots(self, latest: datetime) -> list[datetime]:
        """Slots in the backfill window with no processed record, newest first."""
        window_start = self._window_start()
        done = self.world_event_adapter.processed_export_slots(_floor_to_slot(window_start))
        return [s for s in _expected_slots(window_start, latest) if s not in done]

    def has_new_data(self) -> bool:
        """True when any slot in the backfill window is still unprocessed -- the newest
        one GDELT just published, or an older gap -- or any article preview is pending.
        lastupdate.txt is small, so this is cheap; on a failed read, collect anyway
        (safe fallback)."""
        latest = self._latest_slot()
        if latest is None:
            return True
        if self._pending_slots(latest):
            return True
        return bool(self.world_event_adapter.urls_needing_preview(self._window_start(), 1))

    def _fetch_article_previews(self) -> None:
        """Fetches and stores headline/summary previews for the next batch of source
        URLs that need one. A failure here is logged, never raised -- the cycle's
        event ingestion has already succeeded and shouldn't be reported as failed."""
        try:
            urls = self.world_event_adapter.urls_needing_preview(
                self._window_start(), _MAX_PREVIEWS_PER_CYCLE
            )
            if not urls:
                return
            with ThreadPoolExecutor(max_workers=_PREVIEW_WORKERS) as pool:
                previews = list(pool.map(fetch_article_preview, urls))
            self.world_event_adapter.save_article_previews([
                {
                    "url": url,
                    "status": p.status,
                    "http_status": p.http_status,
                    "headline": p.headline,
                    "summary": p.summary,
                }
                for url, p in zip(urls, previews)
            ])
            counts = Counter(p.status for p in previews)
            logger.info(
                f"World Events: fetched {len(urls)} article preview(s): "
                + ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
            )
        except Exception as e:
            logger.error(f"World Events: article preview pass failed: {e}")

    def _ingest_slot(self, slot: datetime, min_mentions: int) -> int | None:
        """Fetches, filters and upserts one slot's export file, then records it.
        Returns rows upserted, or None if the slot was left pending for a retry."""
        url = self._export_url(slot)
        try:
            rows = _parse_export_rows(_fetch_export_csv(url))
        except ExportFileMissing:
            logger.info(f"World Events: {url} does not exist (404); recording as missing.")
            self.world_event_adapter.mark_export_processed(slot, "missing")
            return 0
        except Exception as e:
            logger.warning(f"World Events: fetching {url} failed, will retry: {e}")
            return None
        rows = [row for row in rows if row["num_mentions"] >= min_mentions]
        if not self.world_event_adapter.upsert_events(rows):
            return None
        self.world_event_adapter.mark_export_processed(slot, "ok", len(rows))
        return len(rows)

    def collect(self) -> None:
        min_mentions = int(self.settings.get("min_mentions", 10))

        latest = self._latest_slot()
        if latest is None:
            logger.error("World Events: could not read the newest slot from lastupdate.txt.")
            return

        pending = self._pending_slots(latest)
        batch = pending[:_MAX_FILES_PER_CYCLE]
        upserted = 0
        retry_later = 0
        for slot in batch:
            n = self._ingest_slot(slot, min_mentions)
            if n is None:
                retry_later += 1
            else:
                upserted += n

        self.world_event_adapter.prune_export_slots(_floor_to_slot(self._window_start()))
        logger.info(
            f"World Events: processed {len(batch) - retry_later}/{len(batch)} export "
            f"file(s), upserted {upserted} event(s); {len(pending) - len(batch)} more "
            f"slot(s) pending for later cycles, {retry_later} to retry."
        )

        self._fetch_article_previews()
