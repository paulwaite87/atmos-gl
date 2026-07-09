from atmos_gl.db.process_status_adapter import FakeProcessStatusAdapter


def test_get_process_status_returns_none_when_never_run():
    adapter = FakeProcessStatusAdapter()
    assert adapter.get_process_status("quakes") is None


def test_record_success_sets_last_updated_and_clears_error():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run("quakes", "collector", success=True)
    row = adapter.get_process_status("quakes")
    assert row["name"] == "quakes"
    assert row["kind"] == "collector"
    assert row["last_updated"] is not None
    assert row["last_error"] is None


def test_record_failure_sets_error_and_leaves_last_updated_untouched():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run("quakes", "collector", success=True)
    first = adapter.get_process_status("quakes")
    first_last_updated = first["last_updated"]

    adapter.record_process_run("quakes", "collector", success=False, error="boom")
    row = adapter.get_process_status("quakes")
    assert row["last_updated"] == first_last_updated
    assert row["last_error"] == "boom"


def test_record_success_after_failure_clears_error_and_advances_last_updated():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run("quakes", "collector", success=False, error="boom")
    adapter.record_process_run("quakes", "collector", success=True)
    row = adapter.get_process_status("quakes")
    assert row["last_error"] is None
    assert row["last_updated"] is not None


def test_first_run_ever_failing_leaves_last_updated_none():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run("quakes", "collector", success=False, error="boom")
    row = adapter.get_process_status("quakes")
    assert row["last_updated"] is None
    assert row["last_error"] == "boom"


def test_get_all_process_status_keys_by_name():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run("quakes", "collector", success=True)
    adapter.record_process_run("storms", "collector", success=True)
    rows = adapter.get_all_process_status()
    assert set(rows.keys()) == {"quakes", "storms"}
    assert rows["quakes"]["kind"] == "collector"
