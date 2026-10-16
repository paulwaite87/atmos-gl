#!/usr/bin/env python3
"""Country Statistics layer data: per-country indicators from Our World in Data (OWID),
each country as close to now as its data gets -- its own most recent year, never a
projected one.

INDICATORS is the layer's catalog, in three GROUPS (the World Data picker's option
groups) -- adding an indicator is one entry here. Each names
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
from datetime import date

from terrascope.lib.country_codes import COUNTRY_CODES

DEFAULT_MAX_AGE_YEARS = 5

# OWID's own codes for real countries -> the code their polygon carries.
CODE_ALIASES = {"OWID_KOS": "KOS"}
_ISO3_RE = re.compile(r"^[A-Z]{3}$")


# The picker's option groups, in order: (id, label).
GROUPS = (
    ("people", "People & Health"),
    ("economy", "Economy & Society"),
    ("environment", "Energy & Environment"),
)
# Survey-based indicators: household surveys run every 5-10 years, so a 5-year cutoff
# would grey out most countries.
_SURVEY_MAX_AGE_YEARS = 10


@dataclass(frozen=True)
class Indicator:
    id: str
    group: str            # a GROUPS id
    label: str            # the picker's option text, and the legend/popup title
    slug: str             # OWID grapher chart: ourworldindata.org/grapher/<slug>
    column: str           # the chart CSV's value column (useColumnShortNames)
    scale: str            # "linear" | "log" -- how values map onto the colour ramp
    colormap: str = "viridis"
    params: str = ""      # a multi-metric chart's own query, e.g. "metric=net_migration_rate"
    diverging: bool = False  # negative and positive values: a domain symmetric about 0
    domain: tuple[float, float] | None = None  # fixed colour range; None: from the data
    max_age_years: int | None = None           # overrides the layer's setting


# Slugs, params and columns are OWID's final charts as confirmed live on 2026-10-06
# (several older slugs redirect to the multi-metric fossil-fuels/energy-mix/
# electricity-mix charts, where the params pick the series). Where a chart also
# carries UN projection variants, `column` is the estimates one.
INDICATORS = (
    # --- People & Health ---
    Indicator("population", "people", "Population", "population", "population_historical", "log"),
    Indicator("median_age", "people", "Median age", "median-age",
              "median_age__sex_all__age_all__variant_estimates", "linear", colormap="magma"),
    Indicator("fertility_rate", "people", "Fertility rate", "children-per-woman-un",
              "fertility_rate__sex_all__age_all__variant_estimates", "linear", colormap="magma"),
    Indicator("population_growth", "people", "Population growth rate", "population-growth-rates",
              "growth_rate__sex_all__age_all__variant_estimates", "linear", colormap="coolwarm",
              diverging=True),
    Indicator("net_migration_rate", "people", "Net migration rate", "net-migration-metrics",
              "net_migration_rate__sex_all__age_all__variant_estimates", "linear",
              colormap="coolwarm", params="metric=net_migration_rate", diverging=True),
    Indicator("urban_share", "people", "Urban population share", "share-of-population-urban",
              "share__area_type_urban__data_type_estimates", "linear"),
    Indicator("life_expectancy", "people", "Life expectancy", "life-expectancy", "life_expectancy_0",
              "linear", colormap="magma"),
    Indicator("child_mortality", "people", "Child mortality", "child-mortality",
              "child_mortality_rate", "log", colormap="ylorrd"),
    Indicator("adult_obesity", "people", "Adult obesity", "share-of-adults-defined-as-obese",
              "obesity_among_adults__bmi__gt__30_kg_m2__crude_estimate__pct__sex_both_sexes__"
              "age_group_18plus__years_of_age", "linear", colormap="ylorrd"),
    Indicator("health_spending", "people", "Health spending (% of GDP)",
              "total-healthcare-expenditure-gdp",
              "current_health_expenditure__che__as_percentage_of_gross_domestic_product__gdp__pct",
              "linear"),
    Indicator("hiv_prevalence", "people", "HIV prevalence", "share-of-the-population-infected-with-hiv",
              "hiv_prevalence__age_15_49__sex_total__estimate_central", "log", colormap="ylorrd"),
    Indicator("suicide_rate", "people", "Suicide rate", "suicide-rate-who-mdb",
              "age_standardized_death_rate_per_100_000_standard_population__sex_both_sexes__"
              "age_group_all_ages", "linear", colormap="ylorrd"),
    Indicator("adult_smoking", "people", "Adults who smoke", "share-of-adults-who-smoke",
              "tobacco_use_pct_age_std__sex_both_sexes", "linear", colormap="ylorrd"),
    Indicator("undernourishment", "people", "Undernourishment", "prevalence-of-undernourishment",
              "_2_1_1_prevalence_of_undernourishment__000000000024000__value__006121__percent",
              "log", colormap="ylorrd"),
    # --- Economy & Society ---
    Indicator("gdp_per_capita", "economy", "GDP per capita (PPP)", "gdp-per-capita-worldbank",
              "ny_gdp_pcap_pp_kd", "log"),
    Indicator("gdp_total", "economy", "GDP (total, PPP)", "gdp-worldbank", "ny_gdp_mktp_pp_kd", "log"),
    Indicator("hdi", "economy", "Human Development Index", "human-development-index", "hdi__sex_total",
              "linear"),
    Indicator("unemployment", "economy", "Unemployment", "unemployment-rate", "sl_uem_totl_zs",
              "linear", colormap="ylorrd"),
    Indicator("extreme_poverty", "economy", "Extreme poverty ($3 a day)",
              "share-of-population-in-extreme-poverty",
              "headcount_ratio__ppp_version_2021__poverty_line_300__welfare_type_income_or_consumption__"
              "table_income_or_consumption_consolidated__survey_comparability_no_spells",
              "linear", colormap="ylorrd", max_age_years=_SURVEY_MAX_AGE_YEARS),
    Indicator("gini", "economy", "Income inequality (Gini)", "gini-coefficient-wb",
              "gini__welfare_type_income_or_consumption__table_income_or_consumption_consolidated__"
              "survey_comparability_no_spells", "linear", colormap="ylorrd",
              params="survey_comparability=no_spells", max_age_years=_SURVEY_MAX_AGE_YEARS),
    Indicator("internet_users", "economy", "Internet users", "share-of-individuals-using-the-internet",
              "it_net_user_zs", "linear"),
    Indicator("mobile_subscriptions", "economy", "Mobile subscriptions (per 100 people)",
              "mobile-cellular-subscriptions-per-100-people", "it_cel_sets_p2", "linear"),
    Indicator("democracy", "economy", "Democracy index", "electoral-democracy-index",
              "electdem_vdem__estimate_best", "linear"),
    Indicator("corruption_perceptions", "economy", "Corruption Perceptions Index",
              "ti-corruption-perception-index", "cpi_score", "linear"),
    Indicator("military_spending", "economy", "Military spending (% of GDP)",
              "military-spending-as-a-share-of-gdp-sipri", "share_gdp", "log", colormap="ylorrd"),
    Indicator("homicide_rate", "economy", "Homicide rate", "homicide-rate-unodc",
              "value__category_total__sex_total__age_total__unit_of_measurement_rate_per_100_000_population",
              "log", colormap="ylorrd"),
    Indicator("life_satisfaction", "economy", "Life satisfaction", "happiness-cantril-ladder",
              "cantril_ladder_score", "linear"),
    # --- Energy & Environment ---
    Indicator("co2_per_capita", "environment", "CO₂ emissions per capita", "co-emissions-per-capita",
              "emissions_total_per_capita", "log", colormap="inferno"),
    Indicator("co2_total", "environment", "CO₂ emissions (total)", "annual-co2-emissions-per-country",
              "emissions_total", "log", colormap="inferno"),
    Indicator("oil_consumption", "environment", "Oil consumption", "fossil-fuels",
              "oil_consumption_twh", "log", colormap="inferno",
              params="fuel=oil&metric=consumption&per_capita=total"),
    Indicator("gas_consumption", "environment", "Gas consumption", "fossil-fuels",
              "gas_consumption_twh", "log", colormap="inferno",
              params="fuel=gas&metric=consumption&per_capita=total"),
    Indicator("coal_consumption", "environment", "Coal consumption", "fossil-fuels",
              "coal_consumption_twh", "log", colormap="inferno",
              params="fuel=coal&metric=consumption&per_capita=total"),
    Indicator("energy_per_capita", "environment", "Energy use per capita", "energy-mix",
              "total_energy_supply_per_capita_kwh", "log", colormap="inferno",
              params="metric=per_capita&source=total"),
    Indicator("renewables_electricity", "environment", "Renewables share of electricity",
              "electricity-mix", "renewable_share_of_electricity__pct", "linear",
              params="frequency=annual&metric=share_of_generation&source=renewables"),
    Indicator("nuclear_electricity", "environment", "Nuclear share of electricity",
              "electricity-mix", "nuclear_share_of_electricity__pct", "linear",
              params="frequency=annual&metric=share_of_generation&source=nuclear"),
    Indicator("electricity_access", "environment", "Access to electricity",
              "share-of-the-population-with-access-to-electricity", "eg_elc_accs_zs", "linear"),
    Indicator("forest_cover", "environment", "Forest cover", "forest-area-as-share-of-land-area",
              "forest_share", "linear"),
    Indicator("protected_land", "environment", "Protected land", "terrestrial-protected-areas",
              "er_lnd_ptld_zs", "linear"),
    Indicator("safe_drinking_water", "environment", "Safely managed drinking water",
              "access-drinking-water-stacked", "wat_sm__residence_total", "linear"),
    Indicator("calorie_supply", "environment", "Daily calorie supply", "daily-per-capita-caloric-supply",
              "daily_calories", "linear", colormap="magma"),
    Indicator("meat_supply", "environment", "Meat supply per person", "meat-supply-per-person",
              "meat__total__00002943__food_available_for_consumption__0645pc__"
              "kilograms_per_year_per_capita", "linear", colormap="magma"),
)
INDICATORS_BY_ID = {i.id: i for i in INDICATORS}
DEFAULT_INDICATOR = INDICATORS[0].id


def catalog() -> list[dict]:
    """The picker's options: each group, in order, with its indicators' ids and labels
    in catalog order -- served in the globe's config data so the frontend never
    hand-copies the list."""
    return [
        {"id": group_id, "label": group_label,
         "indicators": [{"id": i.id, "label": i.label} for i in INDICATORS if i.group == group_id]}
        for group_id, group_label in GROUPS
    ]


def country_code(owid_code: str) -> str | None:
    """The country code an OWID row's `code` stands for; None for an aggregate."""
    code = CODE_ALIASES.get(owid_code, owid_code)
    return code if _ISO3_RE.match(code) else None


def latest_by_country(csv_text: str, column: str, current_year: int | None = None) -> tuple[dict, set]:
    """Each country's value as close to now as the data gets, from an OWID chart CSV:
    ({code: (year, value)}, {codes with no polygon}). Years after `current_year`
    (default: this calendar year) are projections, never "now", and are ignored --
    OWID's population-density chart, for one, runs to 2100. Raises ValueError if the
    CSV lacks `column` -- OWID renamed it, and the catalog entry needs updating."""
    if current_year is None:
        current_year = date.today().year
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
        if year > current_year:
            continue
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
