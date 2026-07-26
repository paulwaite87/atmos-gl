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


def test_record_start_sets_running_status_without_touching_last_updated_or_error():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run("sst", "collector", success=True)
    before = adapter.get_process_status("sst")

    adapter.record_process_start("sst", "collector")
    row = adapter.get_process_status("sst")

    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["last_updated"] == before["last_updated"]  # untouched -- not faked fresh
    assert row["last_error"] is None


def test_record_run_clears_started_at_and_sets_terminal_status_on_success():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_start("sst", "collector")
    adapter.record_process_run("sst", "collector", success=True)
    row = adapter.get_process_status("sst")

    assert row["status"] == "success"
    assert row["started_at"] is None


def test_record_run_clears_started_at_and_sets_terminal_status_on_failure():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_start("sst", "collector")
    adapter.record_process_run("sst", "collector", success=False, error="boom")
    row = adapter.get_process_status("sst")

    assert row["status"] == "failed"
    assert row["started_at"] is None


def test_never_run_row_has_no_status_key_error():
    """get_process_status returns None entirely when nothing has run -- status only
    exists once a row does."""
    adapter = FakeProcessStatusAdapter()
    assert adapter.get_process_status("sst") is None


# --- record_health: decoupled from record_process_run/record_process_start ---

def test_record_health_sets_health_and_detail():
    adapter = FakeProcessStatusAdapter()
    adapter.record_health("flightradar_collector", "collector", "rate_limited", "Rate limited (HTTP 429)")
    row = adapter.get_process_status("flightradar_collector")
    assert row["health"] == "rate_limited"
    assert row["health_detail"] == "Rate limited (HTTP 429)"
    assert row["health_at"] is not None


def test_record_health_clears_with_none():
    adapter = FakeProcessStatusAdapter()
    adapter.record_health("flightradar_collector", "collector", "blocked", "Blocked (HTTP 529)")
    adapter.record_health("flightradar_collector", "collector", None)
    row = adapter.get_process_status("flightradar_collector")
    assert row["health"] is None
    assert row["health_detail"] is None


def test_record_process_run_does_not_clobber_an_existing_health_signal():
    adapter = FakeProcessStatusAdapter()
    adapter.record_health("flightradar_collector", "collector", "rate_limited", "Rate limited (HTTP 429)")
    adapter.record_process_run("flightradar_collector", "collector", success=True)
    row = adapter.get_process_status("flightradar_collector")
    assert row["status"] == "success"
    assert row["health"] == "rate_limited"  # untouched by record_process_run


def test_record_process_start_does_not_clobber_an_existing_health_signal():
    adapter = FakeProcessStatusAdapter()
    adapter.record_health("flightradar_collector", "collector", "blocked", "Blocked (HTTP 529)")
    adapter.record_process_start("flightradar_collector", "collector")
    row = adapter.get_process_status("flightradar_collector")
    assert row["status"] == "running"
    assert row["health"] == "blocked"  # untouched by record_process_start


def test_health_defaults_to_none_on_a_fresh_row():
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run("flightradar_collector", "collector", success=True)
    row = adapter.get_process_status("flightradar_collector")
    assert row["health"] is None
    assert row["health_detail"] is None
