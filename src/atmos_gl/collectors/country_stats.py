#!/usr/bin/env python3
"""Our World in Data -> country_indicators / country_indicator_values: each Country
Statistics indicator (lib/country_stats.py's INDICATORS) as every country's most recent
value.

Two anonymous endpoints per OWID grapher chart, under
data_collector.datasources["country_stats"]:
  * <slug>.metadata.json -- per-column title, unit, citation, lastUpdated
  * <slug>.csv           -- the whole chart, every country and year (~0.3-1.6 MB)

The metadata is fetched every cycle; the CSV only when its lastUpdated differs from the
stored one. OWID's `time=latest` CSV filter can't replace the full download: it returns
only the chart's default selection (the world and continents), not every country.

OWID data is CC BY 4.0 -- the layer's legend credits the original source and OWID.
"""
import logging

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.country_stats_adapter import CountryStatsAdapter
from atmos_gl.lib.country_stats import INDICATORS, latest_by_country

logger = logging.getLogger(__name__)

_QUERY = "v=1&csvType=full&useColumnShortNames=true"
OWID_GRAPHER_URL = "https://ourworldindata.org/grapher"


class CountryStatsCollector(CollectorBase):
    section = "country_stats"
    channel_key = "country_stats"
    datasource_key = "country_stats"

    def __init__(self, config):
        super().__init__(config)
        self.country_stats_adapter = CountryStatsAdapter()

    def _url(self, slug: str, kind: str) -> str:
        base = self.datasource_url(self.datasource_key) or OWID_GRAPHER_URL
        return f"{base}/{slug}.{kind}?{_QUERY}"

    def collect(self) -> None:
        """Refreshes every indicator OWID has updated. One failing indicator doesn't
        stop the rest; its stored data stays as it was, and collect() raises once all
        have been tried so the failure shows on Data Status."""
        failed = []
        for indicator in INDICATORS:
            try:
                self._collect_one(indicator)
            except Exception as e:
                logger.error(f"Country Statistics: {indicator.id} failed: {e}")
                failed.append(indicator.id)
        if failed:
            raise RuntimeError(f"Country Statistics: couldn't refresh {', '.join(failed)}")

    def _collect_one(self, indicator) -> None:
        r = self._get(self._url(indicator.slug, "metadata.json"), timeout=30)
        if r is None:
            raise RuntimeError("couldn't fetch its metadata")
        column = (r.json().get("columns") or {}).get(indicator.column)
        if column is None:
            raise RuntimeError(f"OWID metadata has no column {indicator.column!r}")
        last_updated = column.get("lastUpdated")
        if last_updated and last_updated == self.country_stats_adapter.get_source_last_updated(indicator.id):
            return

        r = self._get(self._url(indicator.slug, "csv"), timeout=60)
        if r is None:
            raise RuntimeError("couldn't fetch its CSV")
        rows, unmatched = latest_by_country(r.text, indicator.column)
        if not rows:
            raise RuntimeError("its CSV had no country values")
        if unmatched:
            logger.info(
                f"Country Statistics: {indicator.id}: no country polygon for "
                f"{', '.join(sorted(unmatched))}; dropped"
            )
        self.country_stats_adapter.save_indicator(indicator.id, {
            "title": column.get("titleShort") or indicator.label,
            "unit": column.get("unit") or None,
            "short_unit": column.get("shortUnit") or None,
            "citation": column.get("citationShort") or None,
            "source_last_updated": last_updated,
            "next_update": column.get("nextUpdate") or None,
        }, rows)
        logger.info(f"Country Statistics: stored {indicator.id} ({len(rows)} countries, OWID {last_updated})")
