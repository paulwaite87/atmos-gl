#!/usr/bin/env python3
"""Tests for GET /api/system_status -- the System Status section's backing route
(routes/system_status.py). Mirrors test_status_route.py's dependency-injection
pattern for the process_status_adapter seam; _database_status()/_disk_status() hit
real infra (DB engine, disk) so those are tested via monkeypatch instead of a live
stack, consistent with this repo's "heavy deps unavailable in dev" testing approach.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from atmos_gl.db.process_status_adapter import FakeProcessStatusAdapter
from atmos_gl.routes.system_status import (
    SERVICE_HEARTBEATS,
    _service_status,
    _database_status,
    _disk_status,
    _dir_size_bytes,
)


def _adapter_with_heartbeat(name: str, age_s: float) -> FakeProcessStatusAdapter:
    adapter = FakeProcessStatusAdapter()
    adapter.record_process_run(name, "service", success=True)
    adapter._rows[name]["last_updated"] = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return adapter


def test_service_status_ok_when_recent():
    meta = SERVICE_HEARTBEATS["data_collector"]
    adapter = _adapter_with_heartbeat("data_collector", age_s=5)
    result = _service_status("data_collector", meta, adapter)
    assert result["status"] == "ok"
    assert result["last_seen"] is not None


def test_service_status_stale_past_warn_threshold():
    meta = SERVICE_HEARTBEATS["data_collector"]
    adapter = _adapter_with_heartbeat("data_collector", age_s=meta["warn_after_s"] + 10)
    result = _service_status("data_collector", meta, adapter)
    assert result["status"] == "stale"


def test_service_status_dead_past_dead_threshold():
    meta = SERVICE_HEARTBEATS["data_collector"]
    adapter = _adapter_with_heartbeat("data_collector", age_s=meta["dead_after_s"] + 10)
    result = _service_status("data_collector", meta, adapter)
    assert result["status"] == "dead"


def test_service_status_unknown_with_no_heartbeat_ever():
    meta = SERVICE_HEARTBEATS["housekeeper"]
    adapter = FakeProcessStatusAdapter()
    result = _service_status("housekeeper", meta, adapter)
    assert result["status"] == "unknown"
    assert result["last_seen"] is None


def test_database_status_ok_on_successful_ping():
    with patch("atmos_gl.routes.system_status.Session") as mock_session_cls:
        mock_session = mock_session_cls.return_value.__enter__.return_value
        mock_session.execute.return_value = None
        result = _database_status()
    assert result["ok"] is True
    assert result["error"] is None
    assert result["latency_ms"] is not None


def test_database_status_reports_error_on_failure():
    with patch("atmos_gl.routes.system_status.Session") as mock_session_cls:
        mock_session_cls.return_value.__enter__.side_effect = RuntimeError("connection refused")
        result = _database_status()
    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_disk_status_reports_percent_used():
    with patch("atmos_gl.routes.system_status.load_config") as mock_load_config:
        mock_load_config.return_value.get_setting.return_value = "."
        with patch("atmos_gl.routes.system_status.shutil.disk_usage") as mock_disk_usage:
            mock_disk_usage.return_value = (1000, 400, 600)
            with patch(
                "atmos_gl.routes.system_status._dir_size_bytes", return_value=250
            ):
                result = _disk_status()
    assert result["percent_used"] == 40.0
    assert result["total_bytes"] == 1000
    assert result["our_data_bytes"] == 250


def test_disk_status_handles_missing_path_gracefully():
    with patch("atmos_gl.routes.system_status.load_config") as mock_load_config:
        mock_load_config.return_value.get_setting.return_value = "/nonexistent"
        with patch(
            "atmos_gl.routes.system_status.shutil.disk_usage",
            side_effect=OSError("no such path"),
        ):
            result = _disk_status()
    assert result["percent_used"] is None
    assert result["our_data_bytes"] is None


def test_dir_size_bytes_sums_nested_files(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x" * 100)
    nested = tmp_path / "fields"
    nested.mkdir()
    (nested / "b.npz").write_bytes(b"y" * 50)
    assert _dir_size_bytes(str(tmp_path)) == 150


def test_dir_size_bytes_returns_none_for_missing_path():
    assert _dir_size_bytes("/definitely/does/not/exist") is None
