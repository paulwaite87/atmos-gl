#!/usr/bin/env python3
"""Tests for FieldStore's real logic (architecture review candidate "lock down the
numeric core with tests"): the atomic .npz write, the store-succeeds-but-catalog-fails
divergence path, reconcile()'s orphan detection, and the two prune methods. Zero
coverage before this, despite FieldStore already taking its FieldCatalogAdapter by
injection with a working FakeFieldCatalogAdapter available.

Deliberately skips the shallow passthroughs (field_exists, get_field_meta,
live_product_hours, get_size_on_disk) -- testing those would mostly assert the Fake
behaves like itself, not that FieldStore does anything (fails the deletion test).
"""
import numpy as np
import pytest

from worldmap.db.field_catalog_adapter import FakeFieldCatalogAdapter
from worldmap.lib.fieldstore import FieldStore


def _unpacked(lat=None, lon=None, u=None, v=None, values=None):
    return {
        "lat": lat if lat is not None else np.array([1.0, 0.0, -1.0]),
        "lon": lon if lon is not None else np.array([-1.0, 0.0, 1.0]),
        "values": values,
        "values2": None,
        "u": u,
        "v": v,
    }


class _RaisingOnUpsertAdapter(FakeFieldCatalogAdapter):
    """A Fake that writes the file fine but fails the catalog upsert -- exercises
    store_field's divergence path (file on disk, no catalog row)."""

    def upsert_field_catalog(self, *args, **kwargs):
        raise RuntimeError("simulated catalog write failure")


# ---- store_field / get_field round-trip ---------------------------------------

def test_store_then_get_roundtrips_arrays(tmp_path):
    store = FieldStore(FakeFieldCatalogAdapter(), str(tmp_path))
    u = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    v = np.array([-1.0, -2.0, -3.0], dtype=np.float32)

    ok = store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=u, v=v))
    assert ok is True

    got = store.get_field("2026-01-01", "00", 3, "waves")
    assert got is not None
    assert np.allclose(got["u"], u)
    assert np.allclose(got["v"], v)
    assert got["values"] is None  # was never set


def test_store_field_writes_the_file_atomically_no_temp_leftovers(tmp_path):
    store = FieldStore(FakeFieldCatalogAdapter(), str(tmp_path))
    store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))

    field_dir = tmp_path / "data" / "fields" / "2026-01-01" / "00"
    files = list(field_dir.iterdir())
    assert files == [field_dir / "waves_f003.npz"]  # no .tmp/partial files left behind


def test_get_field_returns_none_when_not_catalogued(tmp_path):
    store = FieldStore(FakeFieldCatalogAdapter(), str(tmp_path))
    assert store.get_field("2026-01-01", "00", 3, "waves") is None


def test_get_field_treats_catalog_file_divergence_as_a_miss(tmp_path):
    """Catalog row exists but the backing file was deleted out from under it --
    get_field must not crash, and must report a cache miss."""
    adapter = FakeFieldCatalogAdapter()
    store = FieldStore(adapter, str(tmp_path))
    store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))

    field_path = tmp_path / "data" / "fields" / "2026-01-01" / "00" / "waves_f003.npz"
    field_path.unlink()

    assert store.get_field("2026-01-01", "00", 3, "waves") is None


# ---- store_field: catalog-write failure leaves the file for reconcile ---------

def test_store_field_catalog_failure_returns_false_but_keeps_the_file(tmp_path):
    store = FieldStore(_RaisingOnUpsertAdapter(), str(tmp_path))

    ok = store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))

    assert ok is False
    field_path = tmp_path / "data" / "fields" / "2026-01-01" / "00" / "waves_f003.npz"
    assert field_path.exists()  # file survives; reconcile() picks it up later


# ---- reconcile() ----------------------------------------------------------------

def test_reconcile_removes_catalog_rows_whose_file_is_missing(tmp_path):
    adapter = FakeFieldCatalogAdapter()
    store = FieldStore(adapter, str(tmp_path))
    store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))
    store.store_field("2026-01-01", "00", 4, "waves", _unpacked(u=np.zeros(3)))

    # Simulate the f003 file vanishing (e.g. deleted out-of-band) while its catalog
    # row survives.
    (tmp_path / "data" / "fields" / "2026-01-01" / "00" / "waves_f003.npz").unlink()

    store.reconcile()

    assert store.get_field_meta("2026-01-01", "00", 3, "waves") is None  # orphan row gone
    assert store.get_field_meta("2026-01-01", "00", 4, "waves") is not None  # untouched


# ---- prune_except_run -----------------------------------------------------------

def test_prune_except_run_removes_superseded_run_only(tmp_path):
    store = FieldStore(FakeFieldCatalogAdapter(), str(tmp_path))
    store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))
    store.store_field("2026-01-02", "00", 3, "waves", _unpacked(u=np.zeros(3)))

    store.prune_except_run("2026-01-02", "00")

    assert store.get_field_meta("2026-01-01", "00", 3, "waves") is None  # superseded, gone
    assert store.get_field_meta("2026-01-02", "00", 3, "waves") is not None  # current run kept
    old_path = tmp_path / "data" / "fields" / "2026-01-01" / "00" / "waves_f003.npz"
    assert not old_path.exists()  # file deleted too, not just the row


def test_prune_except_run_with_products_filter_leaves_other_products_alone(tmp_path):
    store = FieldStore(FakeFieldCatalogAdapter(), str(tmp_path))
    store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))
    store.store_field("2026-01-01", "00", 3, "currents", _unpacked(u=np.zeros(3)))

    # Prune waves' superseded run, but scope to the waves product family only.
    store.prune_except_run("2026-01-02", "00", products=["waves"])

    assert store.get_field_meta("2026-01-01", "00", 3, "waves") is None
    assert store.get_field_meta("2026-01-01", "00", 3, "currents") is not None  # different family, untouched


# ---- prune_expired ----------------------------------------------------------------

def test_prune_expired_removes_old_rows_and_files(tmp_path):
    from datetime import datetime, timedelta, timezone

    adapter = FakeFieldCatalogAdapter()
    store = FieldStore(adapter, str(tmp_path))
    store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))

    # Backdate the row's updated_at past the expiry window.
    key = ("2026-01-01", "00", 3, "waves")
    adapter._catalog[key]["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=100)

    removed = store.prune_expired(expiry_hours=48)

    assert removed == 1
    assert store.get_field_meta("2026-01-01", "00", 3, "waves") is None
    old_path = tmp_path / "data" / "fields" / "2026-01-01" / "00" / "waves_f003.npz"
    assert not old_path.exists()


def test_prune_expired_keeps_fresh_rows(tmp_path):
    store = FieldStore(FakeFieldCatalogAdapter(), str(tmp_path))
    store.store_field("2026-01-01", "00", 3, "waves", _unpacked(u=np.zeros(3)))

    removed = store.prune_expired(expiry_hours=48)

    assert removed == 0
    assert store.get_field_meta("2026-01-01", "00", 3, "waves") is not None
