from datetime import date, datetime, timedelta, timezone

from worldmap.db.field_catalog_adapter import FakeFieldCatalogAdapter


def _upsert(adapter, run_date, run_id, fhour, product, **kw):
    adapter.upsert_field_catalog(
        run_date=run_date,
        run_id=run_id,
        fhour=fhour,
        product=product,
        nlat=kw.get("nlat", 10),
        nlon=kw.get("nlon", 20),
        valid_time=kw.get("valid_time"),
        storage_uri=kw.get("storage_uri", f"{product}_{fhour}.npz"),
    )


def test_products_with_data_returns_products_of_freshest_run_only():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    _upsert(adapter, date(2026, 7, 1), "00", 0, "temperature")
    _upsert(adapter, date(2026, 7, 2), "00", 0, "wind")
    present = adapter.products_with_data(["wind", "temperature"])
    assert present == ["wind"]


def test_products_with_data_empty_when_no_candidates_have_data():
    adapter = FakeFieldCatalogAdapter()
    assert adapter.products_with_data(["wind"]) == []
    assert adapter.products_with_data([]) == []


def test_get_latest_run_hours_no_products_returns_all_distinct_hours():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    _upsert(adapter, date(2026, 7, 1), "00", 1, "wind")
    _upsert(adapter, date(2026, 7, 2), "12", 3, "wind")
    result = adapter.get_latest_run_hours()
    assert result["run_date"] == date(2026, 7, 2)
    assert result["run_id"] == "12"
    assert result["hours"] == [3]
    assert result["fmin"] == 3
    assert result["fmax"] == 3


def test_get_latest_run_hours_scoped_to_products_requires_all_present():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    _upsert(adapter, date(2026, 7, 1), "00", 0, "temperature")
    _upsert(adapter, date(2026, 7, 1), "00", 1, "wind")  # missing temperature at f001
    result = adapter.get_latest_run_hours(products=["wind", "temperature"])
    assert result["hours"] == [0]


def test_get_latest_run_hours_returns_none_when_catalog_empty():
    adapter = FakeFieldCatalogAdapter()
    assert adapter.get_latest_run_hours() is None


def test_get_latest_run_hours_no_hours_for_scoped_products_still_returns_run():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "currents")
    result = adapter.get_latest_run_hours(products=["wind"])
    assert result is None


def test_upsert_field_catalog_conflict_updates_row():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind", nlat=10, nlon=20)
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind", nlat=99, nlon=99)
    row = adapter.get_field_catalog(date(2026, 7, 1), "00", 0, "wind")
    assert row["nlat"] == 99
    assert row["nlon"] == 99


def test_get_field_catalog_missing_returns_none():
    adapter = FakeFieldCatalogAdapter()
    assert adapter.get_field_catalog(date(2026, 7, 1), "00", 0, "wind") is None


def test_get_product_hours_sorted_for_one_product_in_run():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 3, "wind")
    _upsert(adapter, date(2026, 7, 1), "00", 1, "wind")
    _upsert(adapter, date(2026, 7, 1), "00", 2, "temperature")
    assert adapter.get_product_hours(date(2026, 7, 1), "00", "wind") == [1, 3]


def test_get_live_product_hours_returns_pairs_across_all_runs():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    _upsert(adapter, date(2026, 7, 2), "12", 5, "wind")
    assert adapter.get_live_product_hours() == {("wind", 0), ("wind", 5)}


def test_field_catalog_exists_true_and_false():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    assert adapter.field_catalog_exists(date(2026, 7, 1), "00", 0, "wind") is True
    assert adapter.field_catalog_exists(date(2026, 7, 1), "00", 1, "wind") is False


def test_delete_field_catalog_removes_row():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    adapter.delete_field_catalog(date(2026, 7, 1), "00", 0, "wind")
    assert adapter.get_field_catalog(date(2026, 7, 1), "00", 0, "wind") is None


def test_get_orphan_field_rows_returns_rows_with_missing_files(tmp_path):
    adapter = FakeFieldCatalogAdapter()
    missing_uri = "missing.npz"
    present_uri = "present.npz"
    (tmp_path / present_uri).write_text("x")
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind", storage_uri=missing_uri)
    _upsert(adapter, date(2026, 7, 1), "00", 1, "wind", storage_uri=present_uri)
    orphans = adapter.get_orphan_field_rows(tmp_path)
    assert len(orphans) == 1
    assert orphans[0]["storage_uri"] == missing_uri


def test_get_field_rows_except_excludes_given_run():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    _upsert(adapter, date(2026, 7, 2), "00", 0, "wind")
    rows = adapter.get_field_rows_except(date(2026, 7, 2), "00")
    assert len(rows) == 1
    assert rows[0]["run_date"] == date(2026, 7, 1)


def test_get_field_rows_except_scoped_to_products():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    _upsert(adapter, date(2026, 7, 1), "00", 0, "currents")
    rows = adapter.get_field_rows_except(date(2026, 7, 2), "00", products=["wind"])
    assert len(rows) == 1
    assert rows[0]["product"] == "wind"


def test_get_expired_field_rows_and_prune():
    adapter = FakeFieldCatalogAdapter()
    _upsert(adapter, date(2026, 7, 1), "00", 0, "wind")
    adapter._catalog[(date(2026, 7, 1), "00", 0, "wind")]["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=100)
    )
    _upsert(adapter, date(2026, 7, 2), "00", 0, "wind")

    expired = adapter.get_expired_field_rows(expiry_hours=48)
    assert len(expired) == 1
    assert expired[0]["run_date"] == date(2026, 7, 1)

    removed = adapter.prune_field_catalog(expiry_hours=48)
    assert removed == 1
    assert adapter.get_field_catalog(date(2026, 7, 1), "00", 0, "wind") is None
    assert adapter.get_field_catalog(date(2026, 7, 2), "00", 0, "wind") is not None


def test_enqueue_backfill_is_idempotent_but_resets_failed():
    adapter = FakeFieldCatalogAdapter()
    adapter.enqueue_backfill(date(2026, 7, 1), "00", 3, "wind")
    key = (date(2026, 7, 1), "00", 3, "wind")
    assert adapter._backfill[key]["status"] == "requested"

    # re-enqueue while still requested: no-op
    adapter.enqueue_backfill(date(2026, 7, 1), "00", 3, "wind")
    assert adapter._backfill[key]["attempts"] == 0

    adapter._backfill[key]["status"] = "failed"
    adapter.enqueue_backfill(date(2026, 7, 1), "00", 3, "wind")
    assert adapter._backfill[key]["status"] == "requested"


def test_claim_backfill_requests_only_claims_requested_rows_in_order():
    adapter = FakeFieldCatalogAdapter()
    adapter.enqueue_backfill(date(2026, 7, 1), "00", 1, "wind")
    adapter.enqueue_backfill(date(2026, 7, 1), "00", 2, "wind")
    adapter._backfill[(date(2026, 7, 1), "00", 2, "wind")]["status"] = "done"

    claimed = adapter.claim_backfill_requests(limit=20)
    assert len(claimed) == 1
    assert claimed[0]["fhour"] == 1
    assert claimed[0]["attempts"] == 1
    assert adapter._backfill[(date(2026, 7, 1), "00", 1, "wind")]["status"] == "fetching"


def test_claim_backfill_requests_respects_limit():
    adapter = FakeFieldCatalogAdapter()
    for fhour in range(5):
        adapter.enqueue_backfill(date(2026, 7, 1), "00", fhour, "wind")
    claimed = adapter.claim_backfill_requests(limit=2)
    assert len(claimed) == 2


def test_mark_backfill_sets_status():
    adapter = FakeFieldCatalogAdapter()
    adapter.enqueue_backfill(date(2026, 7, 1), "00", 1, "wind")
    adapter.mark_backfill(date(2026, 7, 1), "00", 1, "wind", "done")
    assert adapter._backfill[(date(2026, 7, 1), "00", 1, "wind")]["status"] == "done"
