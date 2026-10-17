#!/usr/bin/env python3
"""NightLightsCollector (collectors/night_lights.py): GIBS capabilities and image
requests are stubbed at urlopen; assertions are on the cached image and its recorded
date, which is what the render task and the legend read."""

import io
import os
import re
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terrascope.collectors import night_lights as mod
from terrascope.collectors.night_lights import NightLightsCollector
from terrascope.db.process_status_adapter import FakeProcessStatusAdapter
from terrascope.lib.gibs import (
    layer_dates,
    night_lights_cache_path,
    read_night_lights_date,
)

CAPS = (
    Path(__file__).parent / "fixtures" / "night_lights" / "capabilities.xml"
).read_bytes()
BASE = "https://gibs.test/wms/epsg4326/best/wms.cgi"


def png(tag: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag


def make_collector(workdir, settings=None):
    c = NightLightsCollector.__new__(NightLightsCollector)
    c.settings = {"expiry_hours": 12, **(settings or {})}
    c.config = MagicMock()
    c.config.get_setting.side_effect = lambda s, k, d=None: (
        workdir if (s, k) == ("common", "workdir") else d
    )
    c.datasource_url = MagicMock(return_value=BASE)
    c.process_status_adapter = FakeProcessStatusAdapter()
    return c


class FakeGibs:
    """Routes urlopen: capabilities -> CAPS; GetMap -> the body registered for its TIME."""

    def __init__(self, images):
        self.images = images  # {"2026-10-15": bytes | Exception}
        self.requested = []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "GetCapabilities" in url:
            return io.BytesIO(CAPS)
        date = re.search(r"TIME=([\d-]+)", url).group(1)
        self.requested.append(date)
        body = self.images[date]
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(body)


def run(c, gibs):
    with patch.object(mod.urllib.request, "urlopen", gibs):
        c.collect()


def test_collect_fetches_the_newest_date_the_layer_offers(tmp_path):
    gibs = FakeGibs({"2026-10-15": png(b"newest")})
    run(make_collector(str(tmp_path)), gibs)
    assert gibs.requested == ["2026-10-15"]
    assert open(night_lights_cache_path(str(tmp_path)), "rb").read() == png(b"newest")
    assert read_night_lights_date(str(tmp_path)) == "2026-10-15"


def test_a_failing_newest_date_falls_back_to_the_previous_one(tmp_path):
    gibs = FakeGibs(
        {"2026-10-15": urllib.error.URLError("boom"), "2026-10-14": png(b"prev")}
    )
    run(make_collector(str(tmp_path)), gibs)
    assert gibs.requested == ["2026-10-15", "2026-10-14"]
    assert open(night_lights_cache_path(str(tmp_path)), "rb").read() == png(b"prev")
    assert read_night_lights_date(str(tmp_path)) == "2026-10-14"


def test_an_empty_newest_image_falls_back_to_the_previous_one(tmp_path):
    gibs = FakeGibs({"2026-10-15": b"", "2026-10-14": png(b"prev")})
    run(make_collector(str(tmp_path)), gibs)
    assert read_night_lights_date(str(tmp_path)) == "2026-10-14"


def test_layer_dates_lists_newest_first_and_omits_gaps():
    dates = layer_dates(
        CAPS, "VIIRS_SNPP_GapFilled_BRDF_Corrected_DayNightBand_Radiance"
    )
    assert dates[:3] == ["2026-10-15", "2026-10-14", "2026-10-13"]
    assert "2026-08-04" not in dates and "2026-08-05" not in dates
    assert dates.index("2026-08-06") + 1 == dates.index("2026-08-03")


def test_layer_dates_is_empty_for_an_unknown_layer():
    assert layer_dates(CAPS, "No_Such_Layer") == []


def seed_cache(workdir, age_hours, date="2026-10-13"):
    from terrascope.lib.gibs import write_night_lights_date

    path = night_lights_cache_path(workdir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_bytes(png(b"old"))
    write_night_lights_date(workdir, date)
    t = __import__("time").time() - age_hours * 3600
    os.utime(path, (t, t))


def test_a_fresh_cache_is_not_downloaded_again(tmp_path):
    seed_cache(str(tmp_path), age_hours=1)
    gibs = FakeGibs({"2026-10-15": png(b"new")})
    run(make_collector(str(tmp_path)), gibs)
    assert gibs.requested == []
    assert read_night_lights_date(str(tmp_path)) == "2026-10-13"


def test_a_stale_cache_is_refreshed(tmp_path):
    seed_cache(str(tmp_path), age_hours=13)
    run(make_collector(str(tmp_path)), FakeGibs({"2026-10-15": png(b"new")}))
    assert read_night_lights_date(str(tmp_path)) == "2026-10-15"


def test_when_both_dates_fail_the_previous_image_and_date_stay(tmp_path):
    seed_cache(str(tmp_path), age_hours=13)
    boom = urllib.error.URLError("boom")
    run(
        make_collector(str(tmp_path)), FakeGibs({"2026-10-15": boom, "2026-10-14": b""})
    )
    assert open(night_lights_cache_path(str(tmp_path)), "rb").read() == png(b"old")
    assert read_night_lights_date(str(tmp_path)) == "2026-10-13"


def test_a_capabilities_failure_leaves_the_previous_image_and_date(tmp_path):
    seed_cache(str(tmp_path), age_hours=13)

    def down(req, timeout=None):
        raise urllib.error.URLError("down")

    with patch.object(mod.urllib.request, "urlopen", down):
        make_collector(str(tmp_path)).collect()
    assert open(night_lights_cache_path(str(tmp_path)), "rb").read() == png(b"old")
    assert read_night_lights_date(str(tmp_path)) == "2026-10-13"
