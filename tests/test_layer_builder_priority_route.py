#!/usr/bin/env python3
"""Route-level tests for /api/layer_builder/priority -- proxies to layer_builder's own
internal order server (a separate container/process, see routes/layer_builder.py's
docstring), so requests itself is mocked rather than DI-overridden (there's no adapter
seam here, this route holds no state of its own)."""
from unittest.mock import MagicMock, patch

import requests


def _fake_response(status_code, payload):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def test_get_priority_returns_the_upstream_order(client):
    with patch("atmos_gl.routes.layer_builder.requests.get") as mock_get:
        mock_get.return_value = _fake_response(200, {"order": ["isobars", "wind"]})

        resp = client.get("/api/layer_builder/priority")

    assert resp.status_code == 200
    assert resp.json() == {"order": ["isobars", "wind"]}
    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == "http://layer_builder:9100/priority"


def test_get_priority_returns_502_when_layer_builder_is_unreachable(client):
    with patch("atmos_gl.routes.layer_builder.requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError("refused")

        resp = client.get("/api/layer_builder/priority")

    assert resp.status_code == 502


def test_post_priority_forwards_the_payload_and_returns_the_new_order(client):
    with patch("atmos_gl.routes.layer_builder.requests.post") as mock_post:
        mock_post.return_value = _fake_response(200, {"order": ["wind", "isobars"]})

        resp = client.post(
            "/api/layer_builder/priority", json={"sections": ["wind"]}
        )

    assert resp.status_code == 200
    assert resp.json() == {"order": ["wind", "isobars"]}
    assert mock_post.call_args.kwargs["json"] == {"sections": ["wind"]}


def test_post_priority_rejects_an_empty_sections_list(client):
    resp = client.post("/api/layer_builder/priority", json={"sections": []})

    assert resp.status_code == 422  # pydantic validation, never reaches requests


def test_post_priority_surfaces_a_400_from_upstream(client):
    with patch("atmos_gl.routes.layer_builder.requests.post") as mock_post:
        mock_post.return_value = _fake_response(400, {"error": "unknown section(s): bogus"})

        resp = client.post("/api/layer_builder/priority", json={"sections": ["bogus"]})

    assert resp.status_code == 400
    assert "bogus" in resp.json()["detail"]


def test_post_priority_reset_forwards_to_upstream(client):
    with patch("atmos_gl.routes.layer_builder.requests.post") as mock_post:
        mock_post.return_value = _fake_response(200, {"order": ["isobars", "wind"]})

        resp = client.post("/api/layer_builder/priority/reset")

    assert resp.status_code == 200
    assert mock_post.call_args.args[0] == "http://layer_builder:9100/priority/reset"
