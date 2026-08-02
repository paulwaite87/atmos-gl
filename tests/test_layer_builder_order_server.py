#!/usr/bin/env python3
"""End-to-end tests for _start_order_server -- the stdlib HTTP listener that lets
routes/layer_builder.py (on map_api, the public API) reprioritise layer_builder's
in-memory RoundRobinOrder over the agl docker network. Runs a real server on an
OS-assigned port (port=0) rather than mocking the handler, since the whole point is
locking the actual request/response wire format routes/layer_builder.py proxies.
"""
import requests

from atmos_gl.layer_builder import _start_order_server
from atmos_gl.round_robin_order import RoundRobinOrder


def make_running_server():
    order = RoundRobinOrder(["isobars", "precipitation", "wind"])
    server = _start_order_server(order, port=0)
    port = server.server_address[1]
    return server, order, f"http://127.0.0.1:{port}"


def test_get_priority_returns_the_current_order():
    server, order, base = make_running_server()
    try:
        r = requests.get(f"{base}/priority", timeout=2)
        assert r.status_code == 200
        assert r.json() == {"order": ["isobars", "precipitation", "wind"]}
    finally:
        server.shutdown()


def test_post_priority_reorders_and_returns_the_new_order():
    server, order, base = make_running_server()
    try:
        r = requests.post(f"{base}/priority", json={"sections": ["wind"]}, timeout=2)
        assert r.status_code == 200
        assert r.json() == {"order": ["wind", "isobars", "precipitation"]}
        assert order.current() == ["wind", "isobars", "precipitation"]
    finally:
        server.shutdown()


def test_post_priority_rejects_an_unknown_section():
    server, order, base = make_running_server()
    try:
        r = requests.post(f"{base}/priority", json={"sections": ["bogus"]}, timeout=2)
        assert r.status_code == 400
        assert "bogus" in r.json()["error"]
    finally:
        server.shutdown()


def test_post_priority_rejects_a_non_list_payload():
    server, order, base = make_running_server()
    try:
        r = requests.post(f"{base}/priority", json={"sections": "wind"}, timeout=2)
        assert r.status_code == 400
    finally:
        server.shutdown()


def test_post_priority_reset_restores_the_default_order():
    server, order, base = make_running_server()
    try:
        order.reorder(["wind"])
        r = requests.post(f"{base}/priority/reset", timeout=2)
        assert r.status_code == 200
        assert r.json() == {"order": ["isobars", "precipitation", "wind"]}
    finally:
        server.shutdown()


def test_unknown_path_returns_404():
    server, order, base = make_running_server()
    try:
        r = requests.get(f"{base}/nonsense", timeout=2)
        assert r.status_code == 404
    finally:
        server.shutdown()
