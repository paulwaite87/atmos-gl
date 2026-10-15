#!/usr/bin/env python3
"""Country Statistics end to end through its highest seam: CountryStatsCollector.collect()
against stubbed OWID responses into FakeCountryStatsAdapter, then GET
/api/country_stats through the test client (no network, no DB)."""
import json
import logging
from unittest.mock import MagicMock

import pytest

from atmos_gl.api import app
from atmos_gl.collectors.country_stats import CountryStatsCollector
from atmos_gl.db.country_stats_adapter import FakeCountryStatsAdapter
from atmos_gl.lib.country_stats import INDICATORS, INDICATORS_BY_ID
from atmos_gl.routes.country_stats import get_country_stats_adapter

_BASE = "https://owid.example/grapher"

# Cut from the real files (2026-10-05), plus the rows each test needs.
_POPULATION_CSV = """entity,code,year,population_historical
Afghanistan,AFG,2022,40578846
Afghanistan,AFG,2023,41454761
Africa,OWID_AFR,2023,1481233402
Americas,,2023,1035286442
France,FRA,2023,66438826
Kosovo,OWID_KOS,2023,1756374
"Bonaire, Sint Eustatius and Saba",BES,2023,27148
Norway,NOR,2010,4891251
World,OWID_WRL,2023,8091734930
"""

_OIL_CSV = """entity,code,year,oil_consumption_twh
Afghanistan,AFG,2024,12.5
France,FRA,2025,800.25
Norway,NOR,2018,110.0
Norway,NOR,2019,
"""


def _metadata(column, last_updated, title, unit, short_unit, citation="Some Source (2026)"):
    return {"chart": {"title": title}, "columns": {column: {
        "titleShort": title, "unit": unit, "shortUnit": short_unit,
        "citationShort": citation, "lastUpdated": last_updated, "nextUpdate": "2027-07-01",
    }}}


_STANDARD_QUERY = "v=1&csvType=full&useColumnShortNames=true"


def _chart_key(indicator):
    """A stubbed chart's key: its slug, plus the params picking one series of a
    multi-metric chart (oil, gas and coal all live in fossil-fuels)."""
    return f"{indicator.slug}?{indicator.params}" if indicator.params else indicator.slug


class _Owid:
    """Stub OWID: chart key -> (metadata JSON, CSV text). A missing chart fails like
    _get()."""

    def __init__(self):
        self.charts = {
            _chart_key(INDICATORS_BY_ID["population"]): (
                _metadata("population_historical", "2024-07-15", "Population", "people", ""),
                _POPULATION_CSV),
            _chart_key(INDICATORS_BY_ID["oil_consumption"]): (
                _metadata("oil_consumption_twh", "2026-06-30", "Oil consumption",
                          "terawatt-hours", "TWh"),
                _OIL_CSV),
        }
        self.fetched = []

    def get(self, url, timeout=15, **kwargs):
        self.fetched.append(url)
        path, _, query = url.removeprefix(f"{_BASE}/").partition("?")
        slug, _, kind = path.partition(".")
        params = query.removesuffix(_STANDARD_QUERY).rstrip("&")
        key = f"{slug}?{params}" if params else slug
        if key not in self.charts:
            return None
        metadata, csv_text = self.charts[key]
        r = MagicMock()
        if kind == "metadata.json":
            r.json.return_value = metadata
        else:
            r.text = csv_text
        return r


def _collector(owid, adapter=None):
    c = CountryStatsCollector.__new__(CountryStatsCollector)
    c.config = MagicMock()
    c.config.get_setting.side_effect = lambda s, k, d=None: (
        {"country_stats": _BASE} if (s, k) == ("data_collector", "datasources") else d)
    c.settings = {}
    c.country_stats_adapter = adapter or FakeCountryStatsAdapter()
    c._get = owid.get
    return c


@pytest.fixture
def owid():
    return _Owid()


@pytest.fixture
def collected(owid):
    """A collector that has run once against every stubbed chart; charts it can't
    reach (the stub's missing slugs) just fail, so collect() raises -- the stored
    indicators are still there."""
    c = _collector(owid)
    with pytest.raises(RuntimeError):
        c.collect()
    app.dependency_overrides[get_country_stats_adapter] = lambda: c.country_stats_adapter
    return c


def _get(client, **params):
    resp = client.get("/api/country_stats", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_each_country_shows_its_own_latest_value(client, collected):
    body = _get(client, indicator="population")
    assert body["countries"]["AFG"] == {"year": 2023, "value": 41454761}
    assert body["countries"]["FRA"] == {"year": 2023, "value": 66438826}
    assert body["newest_year"] == 2023


def test_aggregates_are_not_countries(client, collected):
    countries = _get(client, indicator="population")["countries"]
    assert not any(code.startswith("OWID_") for code in countries)
    assert "" not in countries


def test_kosovo_is_mapped_from_owids_own_code(client, collected):
    assert _get(client, indicator="population")["countries"]["KOS"] == {"year": 2023, "value": 1756374}


def test_rows_without_a_country_polygon_are_dropped_and_logged(owid, caplog):
    c = _collector(owid)
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        c.collect()
    stored = c.country_stats_adapter.get_indicator("population")
    assert "BES" not in stored["rows"]
    assert any("BES" in r.getMessage() for r in caplog.records)


def test_a_country_older_than_the_cutoff_is_reported_stale(client, collected):
    countries = _get(client, indicator="population", max_age_years=5)["countries"]
    assert countries["NOR"] == {"year": 2010, "stale": True}


def test_a_wider_cutoff_applies_without_a_refetch(client, collected, owid):
    owid.fetched.clear()
    countries = _get(client, indicator="population", max_age_years=15)["countries"]
    assert countries["NOR"] == {"year": 2010, "value": 4891251}
    assert owid.fetched == []


def test_the_cutoff_defaults_to_five_years(client, collected):
    countries = _get(client, indicator="oil_consumption")["countries"]
    # Norway's newest non-empty year is 2018: more than 5 before France's 2025.
    assert countries["NOR"] == {"year": 2018, "stale": True}
    assert countries["AFG"] == {"year": 2024, "value": 12.5}


def test_an_indicators_own_cutoff_overrides_the_setting(client, collected, monkeypatch):
    from atmos_gl.lib import country_stats
    oil = country_stats.INDICATORS_BY_ID["oil_consumption"]
    monkeypatch.setitem(country_stats.INDICATORS_BY_ID, "oil_consumption",
                        country_stats.Indicator(**{**oil.__dict__, "max_age_years": 10}))
    countries = _get(client, indicator="oil_consumption", max_age_years=1)["countries"]
    assert countries["NOR"] == {"year": 2018, "value": 110.0}


def test_metadata_and_display_settings_are_served_with_the_values(client, collected):
    indicator = _get(client, indicator="oil_consumption")["indicator"]
    assert indicator["id"] == "oil_consumption"
    assert indicator["title"] == "Oil consumption"
    assert indicator["unit"] == "terawatt-hours"
    assert indicator["short_unit"] == "TWh"
    assert indicator["citation"] == "Some Source (2026)"
    assert indicator["last_updated"] == "2026-06-30"
    assert indicator["scale"] == "log"
    assert indicator["colormap"]


def test_the_csv_is_only_downloaded_when_owid_has_updated_it(owid):
    c = _collector(owid)
    with pytest.raises(RuntimeError):
        c.collect()
    owid.fetched.clear()
    with pytest.raises(RuntimeError):
        c.collect()
    assert not any(".csv" in url for url in owid.fetched)

    metadata, csv_text = owid.charts["population"]
    metadata["columns"]["population_historical"]["lastUpdated"] = "2026-07-15"
    owid.fetched.clear()
    with pytest.raises(RuntimeError):
        c.collect()
    assert [url for url in owid.fetched if ".csv" in url] == [
        f"{_BASE}/population.csv?v=1&csvType=full&useColumnShortNames=true"]


def test_a_failed_fetch_keeps_the_stored_data(client, collected, owid):
    owid.charts.pop("population")
    with pytest.raises(RuntimeError):
        collected.collect()
    assert _get(client, indicator="population")["countries"]["AFG"]["value"] == 41454761


def test_a_csv_missing_its_value_column_keeps_the_stored_data(client, collected, owid):
    metadata, _ = owid.charts["population"]
    metadata["columns"]["population_historical"]["lastUpdated"] = "2026-07-15"
    owid.charts["population"] = (metadata, "entity,code,year,something_else\nFrance,FRA,2024,1\n")
    with pytest.raises(RuntimeError):
        collected.collect()
    assert _get(client, indicator="population")["countries"]["AFG"]["value"] == 41454761


def test_an_indicator_not_yet_collected_has_no_countries(client):
    app.dependency_overrides[get_country_stats_adapter] = lambda: FakeCountryStatsAdapter()
    body = _get(client, indicator="population")
    assert body["countries"] == {}
    assert body["indicator"]["id"] == "population"
    assert body["newest_year"] is None


def test_an_unknown_indicator_is_rejected(client):
    app.dependency_overrides[get_country_stats_adapter] = lambda: FakeCountryStatsAdapter()
    assert client.get("/api/country_stats", params={"indicator": "nope"}).status_code == 422


def test_every_catalog_indicator_is_fetched_with_its_own_params(owid):
    c = _collector(owid)
    with pytest.raises(RuntimeError):
        c.collect()
    for indicator in INDICATORS:
        params = f"{indicator.params}&" if indicator.params else ""
        assert f"{_BASE}/{indicator.slug}.metadata.json?{params}{_STANDARD_QUERY}" in owid.fetched
    assert (f"{_BASE}/fossil-fuels.csv?fuel=oil&metric=consumption&per_capita=total&"
            f"{_STANDARD_QUERY}") in owid.fetched


def test_collect_succeeds_when_every_indicator_is_fetched(owid):
    for indicator in INDICATORS:
        if _chart_key(indicator) not in owid.charts:
            csv_text = f"entity,code,year,{indicator.column}\nFrance,FRA,2024,1.5\n"
            owid.charts[_chart_key(indicator)] = (
                _metadata(indicator.column, "2026-01-01", indicator.label, "u", "u"), csv_text)
    c = _collector(owid)
    c.collect()
    assert json.dumps(c.country_stats_adapter.get_indicator("population")["metadata"])


def test_owid_is_the_default_source_when_none_is_configured(owid):
    c = _collector(owid)
    c.config.get_setting.side_effect = lambda s, k, d=None: d
    with pytest.raises(RuntimeError):
        c.collect()
    assert owid.fetched[0] == (
        "https://ourworldindata.org/grapher/population.metadata.json?v=1&csvType=full&useColumnShortNames=true")


def test_a_projected_future_year_never_counts_as_now(owid):
    from datetime import date
    future = date.today().year + 1
    metadata, _ = owid.charts["population"]
    owid.charts["population"] = (metadata, (
        "entity,code,year,population_historical\n"
        "France,FRA,2023,66438826\n"
        f"France,FRA,{future},70000000\n"
        "France,FRA,2100,80000000\n"
        "Norway,NOR,2100,6000000\n"))
    c = _collector(owid)
    with pytest.raises(RuntimeError):
        c.collect()
    rows = c.country_stats_adapter.get_indicator("population")["rows"]
    assert rows["FRA"] == (2023, 66438826)
    # A country with only projections has no "now" at all.
    assert "NOR" not in rows


def _survey_chart(owid, indicator_id):
    indicator = INDICATORS_BY_ID[indicator_id]
    owid.charts[_chart_key(indicator)] = (
        _metadata(indicator.column, "2026-01-01", indicator.label, "%", "%"),
        f"entity,code,year,{indicator.column}\nFrance,FRA,2025,1.0\nNorway,NOR,2016,2.0\n"
        "Afghanistan,AFG,2014,3.0\n")


@pytest.mark.parametrize("indicator_id", ["extreme_poverty", "gini"])
def test_survey_indicators_tolerate_ten_year_old_figures(client, owid, indicator_id):
    _survey_chart(owid, indicator_id)
    c = _collector(owid)
    with pytest.raises(RuntimeError):
        c.collect()
    app.dependency_overrides[get_country_stats_adapter] = lambda: c.country_stats_adapter
    countries = _get(client, indicator=indicator_id, max_age_years=5)["countries"]
    assert countries["NOR"] == {"year": 2016, "value": 2.0}
    assert countries["AFG"] == {"year": 2014, "stale": True}


def test_the_title_is_the_catalog_label_not_owids_long_one(client, owid):
    indicator = INDICATORS_BY_ID["adult_obesity"]
    owid.charts[_chart_key(indicator)] = (
        _metadata(indicator.column, "2026-01-01",
                  "Obesity among adults, BMI >= 30 kg/m2 (crude estimate) (%) - Sex: both sexes", "%", None),
        f"entity,code,year,{indicator.column}\nFrance,FRA,2024,10.0\n")
    c = _collector(owid)
    with pytest.raises(RuntimeError):
        c.collect()
    app.dependency_overrides[get_country_stats_adapter] = lambda: c.country_stats_adapter
    assert _get(client, indicator="adult_obesity")["indicator"]["title"] == "Adult obesity"


def test_diverging_indicators_say_so(client):
    app.dependency_overrides[get_country_stats_adapter] = lambda: FakeCountryStatsAdapter()
    assert _get(client, indicator="net_migration_rate")["indicator"]["diverging"] is True
    assert _get(client, indicator="population")["indicator"]["diverging"] is False
