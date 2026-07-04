from worldmap.lib.process_status_repo import FakeProcessStatusRepo


def test_get_process_status_returns_none_when_never_run():
    repo = FakeProcessStatusRepo()
    assert repo.get_process_status("quakes") is None


def test_record_success_sets_last_updated_and_clears_error():
    repo = FakeProcessStatusRepo()
    repo.record_process_run("quakes", "collector", success=True)
    row = repo.get_process_status("quakes")
    assert row["name"] == "quakes"
    assert row["kind"] == "collector"
    assert row["last_updated"] is not None
    assert row["last_error"] is None


def test_record_failure_sets_error_and_leaves_last_updated_untouched():
    repo = FakeProcessStatusRepo()
    repo.record_process_run("quakes", "collector", success=True)
    first = repo.get_process_status("quakes")
    first_last_updated = first["last_updated"]

    repo.record_process_run("quakes", "collector", success=False, error="boom")
    row = repo.get_process_status("quakes")
    assert row["last_updated"] == first_last_updated
    assert row["last_error"] == "boom"


def test_record_success_after_failure_clears_error_and_advances_last_updated():
    repo = FakeProcessStatusRepo()
    repo.record_process_run("quakes", "collector", success=False, error="boom")
    repo.record_process_run("quakes", "collector", success=True)
    row = repo.get_process_status("quakes")
    assert row["last_error"] is None
    assert row["last_updated"] is not None


def test_first_run_ever_failing_leaves_last_updated_none():
    repo = FakeProcessStatusRepo()
    repo.record_process_run("quakes", "collector", success=False, error="boom")
    row = repo.get_process_status("quakes")
    assert row["last_updated"] is None
    assert row["last_error"] == "boom"


def test_get_all_process_status_keys_by_name():
    repo = FakeProcessStatusRepo()
    repo.record_process_run("quakes", "collector", success=True)
    repo.record_process_run("storms", "collector", success=True)
    rows = repo.get_all_process_status()
    assert set(rows.keys()) == {"quakes", "storms"}
    assert rows["quakes"]["kind"] == "collector"
