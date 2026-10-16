#!/usr/bin/env python3
"""NOAA tsunami warning centre CAP feeds -> database.

Pure data (no render): each feed holds only the centre's latest bulletin, so every poll
upserts it into a table that accumulates the history. The frontend reads it via the
/api/tsunami routes -- the browser never calls NOAA or the NWS itself.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from terrascope.collectors.base import CollectorBase
from terrascope.db.tsunami_adapter import TsunamiAdapter

logger = logging.getLogger(__name__)

CATEGORY_RANK = {"Information": 0, "Advisory": 1, "Watch": 2, "Warning": 3}
# Zones are fetched as the public forecast zone first, then as a coastal marine zone: the
# UGC code alone does not say which kind it is.
ZONE_TYPES = ("public", "coastal")
ZONE_URL = "https://api.weather.gov/zones/{type}/{code}"
# Zone geometry runs to ~350 KB apiece, so a cold start spreads the downloads over cycles.
DEFAULT_MAX_ZONES_PER_CYCLE = 25
# A UGC token is a zone number, optionally with its two-letter+Z prefix (otherwise the
# last prefix carries over) and an inclusive ">" range end.
UGC_TOKEN = re.compile(r"^(?:([A-Z]{2}Z))?(\d{3})(?:>(\d{3}))?$")
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}


def _text(node, path) -> str | None:
    found = node.find(path, CAP_NS)
    return found.text.strip() if found is not None and found.text else None


def _parameters(info) -> dict[str, str]:
    return {
        _text(p, "cap:valueName"): _text(p, "cap:value") or ""
        for p in info.findall("cap:parameter", CAP_NS)
    }


def parse_ugc(ugc: str) -> list[str]:
    """Expand an NWSUGC string such as "CAZ042-043-045>047-180148-" into zone codes. The
    trailing purge time (180148) is not a zone token and is dropped."""
    codes, prefix = [], None
    for token in ugc.split("-"):
        m = UGC_TOKEN.match(token)
        if not m:
            continue
        prefix = m.group(1) or prefix
        first, last = int(m.group(2)), int(m.group(3) or m.group(2))
        codes += [f"{prefix}{n:03d}" for n in range(first, last + 1)]
    return codes


def _note(info) -> str | None:
    """The centre's free-text instruction ("a tsunami is not expected"), or None when it
    only says N/A (the Pacific centre's information statements)."""
    text = re.sub(r"\s+", " ", _text(info, "cap:instruction") or "")
    return None if text in ("", "N/A") else text


def _category(info) -> str:
    """A product's category: the CAP-TSU eventCode when present (warning-level products),
    else the event name ("Tsunami Information" for information statements)."""
    code = _text(info, "cap:eventCode/cap:value")
    return code or _text(info, "cap:event").removeprefix("Tsunami ")


class TsunamiCollector(CollectorBase):
    # Zone codes the NWS could not resolve (retired zones). Process-level, because the
    # collector instance is recreated every cycle, so they are not re-requested each time.
    _unresolved: set[str] = set()

    section = "tsunami"
    channel_key = "tsunami"
    datasource_key = "tsunami"

    def __init__(self, config):
        super().__init__(config)
        self.tsunami_adapter = TsunamiAdapter()

    @staticmethod
    def _event_from_cap(document: str) -> dict:
        """One CAP alert -> the adapter's event dict."""
        alert = ET.fromstring(document)
        centre, bulletin_number, event_id = _text(alert, "cap:identifier").split("-", 2)
        infos = alert.findall("cap:info", CAP_NS)
        info = infos[0]
        params = _parameters(info)
        lat, lon = params["EventLatLon"].split(" ")[0].split(",")
        zones: dict[str, str] = {}
        for i in infos:
            for code in parse_ugc(_parameters(i).get("NWSUGC", "")):
                if CATEGORY_RANK[_category(i)] >= CATEGORY_RANK.get(
                    zones.get(code), -1
                ):
                    zones[code] = _category(i)
        return {
            "zones": zones,
            "centre": centre,
            "event_id": event_id,
            "category": max((_category(i) for i in infos), key=CATEGORY_RANK.get),
            "bulletin_number": int(bulletin_number),
            "location_name": re.sub(r"\s+", " ", params["EventLocationName"]),
            "magnitude": float(params["EventPreliminaryMagnitude"]),
            "magnitude_type": params["EventPreliminaryMagnitudeType"],
            "depth_km": float(params["EventDepth"].split(" ")[0]),
            "origin_time": datetime.fromisoformat(params["EventOriginTime"]),
            "expires": datetime.fromisoformat(_text(info, "cap:expires")),
            "bulletin_url": _text(info, "cap:web"),
            "note": _note(info),
            "lat": float(lat),
            "lon": float(lon),
        }

    def collect(self) -> None:
        """Fetch each centre's latest CAP bulletin and upsert its event."""
        for url in self.settings["feeds"]:
            r = self._get(url, timeout=30)
            if r is None:
                logger.error(f"Tsunami: fetch failed for {url!r}.")
                continue
            try:
                event = self._event_from_cap(r.text)
            except (ET.ParseError, AttributeError, KeyError, ValueError) as e:
                logger.error(f"Tsunami: unreadable CAP from {url!r}: {e!r}")
                continue
            if event["category"] == "Information" and not self.settings.get(
                "include_information"
            ):
                continue
            self.tsunami_adapter.upsert_event(event)
            self._fetch_zones(event["zones"])

    def _fetch_zones(self, codes) -> None:
        """Download and cache the geometry of any zone not already held, up to the
        per-cycle limit (misses are cheap and do not count). A zone the NWS cannot resolve under either type is logged and
        skipped."""
        known = self.tsunami_adapter.known_zone_codes()
        budget = int(
            self.settings.get("max_zones_per_cycle", DEFAULT_MAX_ZONES_PER_CYCLE)
        )
        for code in sorted(set(codes) - known - self._unresolved):
            if budget <= 0:
                return
            for zone_type in ZONE_TYPES:
                r = self._get(
                    ZONE_URL.format(type=zone_type, code=code),
                    timeout=60,
                    headers={"Accept": "application/geo+json"},
                )
                if r is not None:
                    self.tsunami_adapter.save_zone(code, r.json()["geometry"])
                    budget -= 1
                    break
            else:
                logger.warning(f"Tsunami: zone {code} not found at the NWS; skipping.")
                self._unresolved.add(code)
