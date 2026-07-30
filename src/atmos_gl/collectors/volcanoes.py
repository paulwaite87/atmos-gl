#!/usr/bin/env python3
"""GVP Weekly Volcanic Activity Report + USGS HANS -> database (issue #253).

Pure data (no render): fetches the Smithsonian/USGS GVP GeoRSS feed (global, weekly
"New"/"Continuing" activity) and the USGS HANS getElevatedVolcanoes JSON (US-only,
richer alert-level detail), joins them on the shared Smithsonian VNUM, and upserts one
current-state row per volcano. The frontend reads them via the /api/volcanoes route.

Replaces the old static NOAA HazEL historical-catalog collector entirely -- no
has_new_data()/HEAD-check optimization (both feeds are cheap enough to always fetch in
full; skipping on GVP's own staleness would risk missing a HANS-only alert-level
change), no history retention. Liveness is last_seen_at-driven (bumped by presence in
EITHER source each poll) -- see VolcanicActivityAdapter.upsert_activity and
Housekeeper.prune_expired_activity.
"""
import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.volcanic_activity_adapter import VolcanicActivityAdapter

logger = logging.getLogger(__name__)

_GEORSS_NS = {"georss": "http://www.georss.org/georss"}
_VNUM_RE = re.compile(r"vn_(\d+)")
# "{Volcano} ({Country}) - Report for {date range} - {Activity Type}" -- GVP's own
# title format (confirmed live, e.g. "Great Sitkin (United States) - Report for 1
# January-7 January 2026 - Continuing Eruptive Activity"). Date ranges use an
# unspaced hyphen ("1 January-7 January"), so splitting on " - " (spaced) cleanly
# separates the three segments.
_NAME_COUNTRY_RE = re.compile(r"^(.*)\s+\(([^)]+)\)\s*$")


def _parse_guid_vnum(guid: str | None) -> str | None:
    """Extracts the Smithsonian VNUM from a GVP GeoRSS <guid> (e.g. "...#vn_311120"
    -> "311120"), or None if the guid carries no vn_ fragment."""
    if not guid:
        return None
    m = _VNUM_RE.search(guid)
    return m.group(1) if m else None


def _parse_gvp_title(title: str | None) -> dict | None:
    """Splits a GVP report <title> into name/country/activity_type, or None if it
    doesn't match the expected three-segment format at all (logged and skipped by the
    caller rather than raising -- a single malformed title shouldn't abort the poll)."""
    if not title:
        return None
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) < 3:
        return None
    name_country, activity_type = parts[0], parts[-1]
    m = _NAME_COUNTRY_RE.match(name_country)
    if m:
        name, country = m.group(1).strip(), m.group(2).strip()
    else:
        name, country = name_country, None
    return {"name": name, "country": country, "activity_type": activity_type}


def _strip_html(text: str | None) -> str | None:
    """Plain-text collapse of a GVP <description>'s raw HTML -- confirmed live: the
    feed delivers report text wrapped in <p> tags (via CDATA, so ElementTree hands it
    back as literal "<p>...</p>" text, not child elements). report_description is
    rendered by inserting straight into the frontend popup's setHTML() call, so a
    <p> tag in there becomes a REAL block element -- always starting its own line,
    which broke the "Report: <text>" row's single-line layout regardless of the
    label styling. Stripped and whitespace-collapsed here, once, at collection time,
    rather than papering over it in every render/frontend consumer."""
    if not text:
        return text
    collapsed = " ".join(re.sub(r"<[^>]+>", " ", text).split())
    return collapsed or None


def _parse_georss_point(text: str | None) -> tuple[float, float] | tuple[None, None]:
    """"<lat> <lon>" (georss:point's space-separated form) -> (lat, lon) floats, or
    (None, None) if absent/malformed."""
    if not text:
        return None, None
    parts = text.split()
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


class VolcanicActivityCollector(CollectorBase):
    section = "volcanoes"
    channel_key = "volcanoes"

    def __init__(self, config):
        super().__init__(config)
        self.activity_adapter = VolcanicActivityAdapter()

    def source_url(self) -> str | None:
        """GVP and HANS live in data_collector.datasources under two keys (gvp/hans),
        not the single datasource_key CollectorBase.source_url() looks up --
        overridden here to try both, GVP first as the global primary source shown,
        matching StormsCollector's jtwc/nhc pattern."""
        return self.datasource_url("gvp") or self.datasource_url("hans") or None

    def _fetch_gvp(self, url: str) -> list[dict]:
        """Fetches and parses the GVP GeoRSS feed into a list of
        {vnum, name, country, activity_type, report_description, lat, lon} dicts.
        Items with no parseable vnum or title are logged and skipped."""
        items = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AtmosGL-Collector/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                root = ET.fromstring(resp.read())
        except Exception as e:
            logger.error(f"Volcanoes: GVP fetch failed: {e}")
            return items

        for item in root.iter("item"):
            guid_el = item.find("guid")
            vnum = _parse_guid_vnum(guid_el.text if guid_el is not None else None)
            if vnum is None:
                logger.debug("Volcanoes: GVP item has no parseable vnum in guid; skipping.")
                continue

            title_el = item.find("title")
            parsed_title = _parse_gvp_title(title_el.text if title_el is not None else None)
            if parsed_title is None:
                logger.warning(f"Volcanoes: GVP item {vnum} has an unparseable title; skipping.")
                continue

            point_el = item.find("georss:point", _GEORSS_NS)
            lat, lon = _parse_georss_point(point_el.text if point_el is not None else None)

            description_el = item.find("description")
            report_description = _strip_html(description_el.text if description_el is not None else None)

            items.append(
                {
                    "vnum": vnum,
                    "name": parsed_title["name"],
                    "country": parsed_title["country"],
                    "activity_type": parsed_title["activity_type"],
                    "report_description": report_description,
                    "lat": lat,
                    "lon": lon,
                }
            )
        return items

    def _fetch_hans(self, url: str) -> list[dict]:
        """Fetches USGS HANS's getElevatedVolcanoes JSON into a list of
        {vnum, color_code, alert_level, notice_url} dicts."""
        items = []
        try:
            req = urllib.request.Request(
                url, headers={"Accept": "application/json", "User-Agent": "AtmosGL-Collector/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Volcanoes: HANS fetch failed: {e}")
            return items

        for r in data if isinstance(data, list) else data.get("volcanoes", []):
            vnum = str(r.get("vnum") or "").strip()
            if not vnum:
                continue
            items.append(
                {
                    "vnum": vnum,
                    "color_code": r.get("color_code"),
                    "alert_level": r.get("alert_level"),
                    "notice_url": r.get("notice_url"),
                }
            )
        return items

    def collect(self) -> None:
        gvp_url = self.datasource_url("gvp")
        hans_url = self.datasource_url("hans")

        gvp_by_vnum = {r["vnum"]: r for r in (self._fetch_gvp(gvp_url) if gvp_url else [])}
        hans_by_vnum = {r["vnum"]: r for r in (self._fetch_hans(hans_url) if hans_url else [])}

        count = 0
        for vnum in gvp_by_vnum.keys() | hans_by_vnum.keys():
            gvp = gvp_by_vnum.get(vnum)
            hans = hans_by_vnum.get(vnum)
            if gvp is None:
                logger.debug(
                    f"Volcanoes: {vnum} is HANS-elevated but absent from this week's GVP "
                    "report; keeping any previously-recorded coordinate/name."
                )
            self.activity_adapter.upsert_activity(
                vnum,
                gvp["name"] if gvp else None,
                gvp["country"] if gvp else None,
                gvp["lat"] if gvp else None,
                gvp["lon"] if gvp else None,
                gvp["activity_type"] if gvp else None,
                gvp["report_description"] if gvp else None,
                hans["color_code"] if hans else None,
                hans["alert_level"] if hans else None,
                hans["notice_url"] if hans else None,
            )
            count += 1
        logger.info(f"Volcanoes: upserted {count} record(s).")
