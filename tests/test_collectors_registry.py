#!/usr/bin/env python3
"""Tests for the canonical collector registry (architecture review candidate #4: "One
canonical collector registry"). FIELD_COLLECTOR_CLASSES/EMBEDDABLE_COLLECTORS/
resolve_embeddable now live once in terrascope.collectors, and terrascope.collectors.service
and terrascope.routes.status both import the same objects -- these tests guard against the
two re-diverging into hand-copied lists.
"""
import terrascope.collectors as collectors
import terrascope.collectors.service as service
import terrascope.routes.status as status


def test_service_shares_the_canonical_field_collector_classes():
    assert service.FIELD_COLLECTOR_CLASSES is collectors.FIELD_COLLECTOR_CLASSES


def test_status_shares_the_canonical_field_collector_classes():
    assert status.FIELD_COLLECTOR_CLASSES is collectors.FIELD_COLLECTOR_CLASSES


def test_service_shares_the_canonical_embeddable_collectors():
    assert service.EMBEDDABLE_COLLECTORS is collectors.EMBEDDABLE_COLLECTORS


def test_status_shares_the_canonical_embeddable_collectors():
    assert status.EMBEDDABLE_COLLECTORS is collectors.EMBEDDABLE_COLLECTORS


def test_resolve_embeddable_resolves_known_names():
    from terrascope.collectors.shipping import ShippingCollector
    from terrascope.collectors.lightning import LightningCollector

    assert collectors.resolve_embeddable("shipping_collector") is ShippingCollector
    assert collectors.resolve_embeddable("lightning_collector") is LightningCollector


def test_resolve_embeddable_returns_none_for_unknown_name():
    assert collectors.resolve_embeddable("not_a_real_collector") is None
