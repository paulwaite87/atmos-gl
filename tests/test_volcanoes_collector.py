#!/usr/bin/env python3
"""Tests for VolcanicActivityCollector (issue #253): GVP title/guid/georss-point
parsing (pure functions, tested directly against fixture text) and collect()'s
GVP/HANS join (tested with mocked HTTP against a FakeVolcanicActivityAdapter-equivalent
MagicMock, asserting the resulting upsert_activity calls).
"""
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.volcanoes import (
    VolcanicActivityCollector,
    _fix_mangled_punctuation,
    _parse_georss_point,
    _parse_guid_vnum,
    _parse_gvp_title,
)

_GVP_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:georss="http://www.georss.org/georss">
<channel>
<item>
<title>Great Sitkin (United States) - Report for 1 January-7 January 2026 - Continuing Eruptive Activity</title>
<description>Lava effusion continues within the crater.</description>
<guid>https://volcano.si.edu/volcano.cfm?vn=311120#vn_311120</guid>
<georss:point>52.076 -176.13</georss:point>
</item>
</channel>
</rss>
"""

_HANS_FIXTURE = b"""
[
  {"volcano_name": "Great Sitkin", "vnum": "311120", "color_code": "ORANGE", "alert_level": "WATCH", "notice_url": "https://example.com/1"},
  {"volcano_name": "Some Other", "vnum": "999999", "color_code": "YELLOW", "alert_level": "ADVISORY", "notice_url": "https://example.com/2"}
]
"""


def make_collector(gvp_url=None, hans_url=None):
    c = VolcanicActivityCollector.__new__(VolcanicActivityCollector)
    c.config = MagicMock()
    datasources = {}
    if gvp_url:
        datasources["gvp"] = gvp_url
    if hans_url:
        datasources["hans"] = hans_url
    c.config.get_setting.return_value = datasources
    return c


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_parse_guid_vnum_extracts_the_smithsonian_number():
    assert _parse_guid_vnum("https://volcano.si.edu/volcano.cfm?vn=311120#vn_311120") == "311120"


def test_parse_guid_vnum_returns_none_when_absent():
    assert _parse_guid_vnum("https://volcano.si.edu/volcano.cfm?vn=311120") is None
    assert _parse_guid_vnum(None) is None


def test_parse_gvp_title_splits_name_country_activity_type():
    parsed = _parse_gvp_title(
        "Great Sitkin (United States) - Report for 1 January-7 January 2026 - "
        "Continuing Eruptive Activity"
    )
    assert parsed == {
        "name": "Great Sitkin",
        "country": "United States",
        "activity_type": "Continuing Eruptive Activity",
    }


def test_parse_gvp_title_handles_a_name_with_no_country_parens():
    parsed = _parse_gvp_title("Unnamed Seamount - Report for 1-7 January 2026 - New Unrest")
    assert parsed == {"name": "Unnamed Seamount", "country": None, "activity_type": "New Unrest"}


def test_parse_gvp_title_returns_none_for_unparseable_text():
    assert _parse_gvp_title("Not a real GVP title") is None
    assert _parse_gvp_title(None) is None


def test_parse_georss_point_splits_lat_lon():
    assert _parse_georss_point("52.076 -176.13") == (52.076, -176.13)


def test_parse_georss_point_returns_none_none_for_malformed_or_missing():
    assert _parse_georss_point(None) == (None, None)
    assert _parse_georss_point("not-a-point") == (None, None)


def test_fix_mangled_punctuation_repairs_the_possessive_pattern():
    assert _fix_mangled_punctuation("Etna?s summit craters") == "Etna’s summit craters"
    assert _fix_mangled_punctuation(
        "Instituto Geofísico del Perú?s (IGP) Centro"
    ) == "Instituto Geofísico del Perú’s (IGP) Centro"


def test_fix_mangled_punctuation_repairs_the_hawaiian_okina_pattern():
    # Halema?uma?u -> Halema'uma'u (properly Halemaʻumaʻu, Kilauea's summit caldera):
    # the Hawaiian ʻokina glottal stop has no ISO-8859-1 representation either, same
    # root cause as the possessive apostrophe case, and gets caught by the same
    # letter-?-letter pattern, not a second special case.
    assert _fix_mangled_punctuation("the Halema?uma?u Crater floor") == (
        "the Halema’uma’u Crater floor"
    )


def test_fix_mangled_punctuation_leaves_a_genuine_question_mark_alone():
    # A real question mark always has a space or the string boundary on at least one
    # side, never a letter directly on both sides -- so this pattern shouldn't misfire.
    assert _fix_mangled_punctuation("Is it erupting? Seismicity remains elevated.") == (
        "Is it erupting? Seismicity remains elevated."
    )


def test_fix_mangled_punctuation_leaves_a_digit_range_alone():
    # A "?" between two DIGITS (e.g. a genuine range/placeholder) is left alone --
    # only letter-?-letter is treated as this encoding artifact.
    assert _fix_mangled_punctuation("a magnitude 5?7 range") == "a magnitude 5?7 range"


def test_fix_mangled_punctuation_passes_through_none_and_empty_string():
    assert _fix_mangled_punctuation(None) is None
    assert _fix_mangled_punctuation("") == ""


def test_source_url_tries_gvp_then_hans():
    c = make_collector(gvp_url="https://gvp.example/feed.xml")
    assert c.source_url() == "https://gvp.example/feed.xml"

    c = make_collector(hans_url="https://hans.example/api")
    assert c.source_url() == "https://hans.example/api"

    c = make_collector()
    assert c.source_url() is None


def test_collect_joins_gvp_and_hans_by_vnum():
    c = make_collector(gvp_url="https://gvp.example/feed.xml", hans_url="https://hans.example/api")
    c.activity_adapter = MagicMock()

    responses = {
        "https://gvp.example/feed.xml": _FakeResponse(_GVP_FIXTURE),
        "https://hans.example/api": _FakeResponse(_HANS_FIXTURE),
    }

    def fake_urlopen(req, timeout=30):
        return responses[req.full_url]

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        c.collect()

    calls_by_vnum = {call.args[0]: call.args for call in c.activity_adapter.upsert_activity.call_args_list}
    assert set(calls_by_vnum) == {"311120", "999999"}

    # GVP+HANS joined record.
    joined = calls_by_vnum["311120"]
    assert joined[1] == "Great Sitkin"
    assert joined[2] == "United States"
    assert joined[3] == 52.076
    assert joined[4] == -176.13
    assert joined[5] == "Continuing Eruptive Activity"
    assert joined[7] == "ORANGE"
    assert joined[8] == "WATCH"

    # HANS-only record (not in this week's GVP report): no name/country/lat/lon.
    hans_only = calls_by_vnum["999999"]
    assert hans_only[1] is None
    assert hans_only[2] is None
    assert hans_only[3] is None
    assert hans_only[4] is None
    assert hans_only[8] == "ADVISORY"


def test_collect_with_no_datasources_configured_upserts_nothing():
    c = make_collector()
    c.activity_adapter = MagicMock()

    c.collect()

    c.activity_adapter.upsert_activity.assert_not_called()
