#!/usr/bin/env python3
"""Country Statistics layer data: per-country indicators from Our World in Data (OWID),
each country at its own most recent year.

INDICATORS is the layer's catalog -- adding an indicator is one entry here. Each names
an OWID grapher chart (`slug`) and that chart's value column (`column`, its short name
in the CSV); the title, units and citation come from OWID's own metadata at collect time.

OWID's full-chart CSV is `entity,code,year,<column>[,...]`. `code` is ISO 3166-1
alpha-3 for countries; everything else is an aggregate -- OWID_* (continents, income
groups, the world, historical states like the USSR), UN_* / WB_* regions, or blank --
except Kosovo, which OWID codes OWID_KOS (CODE_ALIASES). Codes with no polygon in the
bundled geometry (lib/country_codes.py) have nowhere to be drawn and are dropped.
"""
import csv
import io
import re
from dataclasses import dataclass

from atmos_gl.lib.country_codes import COUNTRY_CODES

DEFAULT_MAX_AGE_YEARS = 5

# OWID's own codes for real countries -> the code their polygon carries.
CODE_ALIASES = {"OWID_KOS": "KOS"}
_ISO3_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class Indicator:
    id: str
    label: str            # the settings select's option text
    slug: str             # OWID grapher chart: ourworldindata.org/grapher/<slug>
    column: str           # the chart CSV's value column (useColumnShortNames)
    scale: str            # "linear" | "log" -- how values map onto the colour ramp
    colormap: str = "viridis"
    domain: tuple[float, float] | None = None  # fixed colour range; None: from the data
    max_age_years: int | None = None           # overrides the layer's setting


INDICATORS = (
    Indicator("population", "Population", "population", "population_historical", "log"),
    Indicator("life_expectancy", "Life expectancy", "life-expectancy", "life_expectancy_0",
              "linear", colormap="magma"),
    Indicator("gdp_per_capita", "GDP per capita (PPP)", "gdp-per-capita-worldbank",
              "ny_gdp_pcap_pp_kd", "log", colormap="ylorrd"),
    Indicator("oil_consumption", "Oil consumption", "oil-consumption-by-country",
              "oil_consumption_twh", "log", colormap="inferno"),
)
INDICATORS_BY_ID = {i.id: i for i in INDICATORS}
DEFAULT_INDICATOR = INDICATORS[0].id


def country_code(owid_code: str) -> str | None:
    """The country code an OWID row's `code` stands for; None for an aggregate."""
    code = CODE_ALIASES.get(owid_code, owid_code)
    return code if _ISO3_RE.match(code) else None


def latest_by_country(csv_text: str, column: str) -> tuple[dict, set]:
    """Each country's most recent non-empty value in an OWID chart CSV:
    ({code: (year, value)}, {codes with no polygon}). Raises ValueError if the CSV
    lacks `column` -- OWID renamed it, and the catalog entry needs updating."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if column not in (reader.fieldnames or []):
        raise ValueError(f"column {column!r} not in {reader.fieldnames}")
    latest, unmatched = {}, set()
    for row in reader:
        code = country_code(row["code"])
        raw = row[column]
        if code is None or raw in ("", None):
            continue
        if code not in COUNTRY_CODES:
            unmatched.add(code)
            continue
        year, value = int(row["year"]), float(raw)
        if code not in latest or year > latest[code][0]:
            latest[code] = (year, value)
    return latest, unmatched


def max_age_years(indicator: Indicator, setting) -> int:
    """The staleness cutoff for `indicator`: its own override, else the layer's setting."""
    if indicator.max_age_years is not None:
        return indicator.max_age_years
    return DEFAULT_MAX_AGE_YEARS if setting is None else int(setting)


def country_entries(rows: dict, max_age: int) -> tuple[dict, int | None]:
    """({code: {year, value} | {year, stale: True}}, the newest year of any country).
    A country more than `max_age` years behind the newest is stale: its value is
    withheld, its year kept so the popup can say how old the latest figure is."""
    if not rows:
        return {}, None
    newest = max(year for year, _ in rows.values())
    entries = {
        code: ({"year": year, "stale": True} if newest - year > max_age
               else {"year": year, "value": value})
        for code, (year, value) in rows.items()
    }
    return entries, newest
