#!/usr/bin/env python3
"""CollectorBase.settings_section: an optional override letting a collector read its
settings from a DIFFERENT config section than the one it uses for scheduling/status
identity (self.section). Needed by the greenhouse_gases layer's two collectors
(GeosCfGhgCollector, CamsEgg4BaselineCollector), which share one config section
("greenhouse_gases") but must keep independent Data Status rows / _drive() scheduling
-- the same section-vs-identity split FieldCollectorBase already has via status_name,
here for plain CollectorBase subclasses instead.

Default (unset) must be a no-op: every existing CollectorBase subclass (sst, clouds,
quakes, ...) reads settings from self.section exactly as before.
"""
from unittest.mock import MagicMock

from atmos_gl.collectors.base import CollectorBase


def make_config(sections: dict):
    config = MagicMock()
    config.get_section.side_effect = lambda name: sections.get(name)
    return config


def test_settings_read_from_section_by_default():
    class PlainCollector(CollectorBase):
        section = "sst"

    config = make_config({"sst": {"enabled": True}})
    c = PlainCollector(config)

    assert c.settings == {"enabled": True}
    config.get_section.assert_called_with("sst")


def test_settings_read_from_settings_section_override_when_set():
    class SharedSettingsCollector(CollectorBase):
        section = "ghg_geoscf"
        settings_section = "greenhouse_gases"

    config = make_config({"greenhouse_gases": {"species": "co2"}})
    c = SharedSettingsCollector(config)

    assert c.settings == {"species": "co2"}
    config.get_section.assert_called_with("greenhouse_gases")
