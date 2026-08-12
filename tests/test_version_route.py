#!/usr/bin/env python3
from atmos_gl.api import app
from fastapi.testclient import TestClient


def test_version_reports_the_app_version_env_var(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.2.3")
    resp = TestClient(app).get("/api/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "v1.2.3"}


def test_version_defaults_to_unknown_when_unset(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    resp = TestClient(app).get("/api/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "unknown"}
