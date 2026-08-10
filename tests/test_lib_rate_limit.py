#!/usr/bin/env python3
"""Unit tests for RateLimiter (issue #307) -- a framework-agnostic, in-memory
sliding-window limiter. Time is injected via a fake clock rather than sleeping or
mocking time.monotonic, so these run instantly and deterministically."""
import pytest
from fastapi import HTTPException

from atmos_gl.lib.rate_limit import RateLimiter


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _limiter(max_requests, window_seconds):
    clock = _FakeClock()
    return RateLimiter(max_requests, window_seconds, clock=clock), clock


def test_requests_under_the_limit_are_allowed():
    limiter, _ = _limiter(max_requests=3, window_seconds=60)

    limiter.enforce("1.2.3.4")
    limiter.enforce("1.2.3.4")
    limiter.enforce("1.2.3.4")  # third of three -- still allowed


def test_the_request_that_exceeds_the_limit_raises_429():
    limiter, _ = _limiter(max_requests=2, window_seconds=60)
    limiter.enforce("1.2.3.4")
    limiter.enforce("1.2.3.4")

    with pytest.raises(HTTPException) as exc_info:
        limiter.enforce("1.2.3.4")

    assert exc_info.value.status_code == 429


def test_the_429_carries_a_retry_after_header():
    limiter, _ = _limiter(max_requests=1, window_seconds=60)
    limiter.enforce("1.2.3.4")

    with pytest.raises(HTTPException) as exc_info:
        limiter.enforce("1.2.3.4")

    retry_after = int(exc_info.value.headers["Retry-After"])
    assert retry_after > 0


def test_different_keys_are_tracked_independently():
    limiter, _ = _limiter(max_requests=1, window_seconds=60)
    limiter.enforce("1.2.3.4")

    limiter.enforce("5.6.7.8")  # a different key -- unaffected by 1.2.3.4's hit


def test_a_rejected_attempt_is_not_itself_recorded_as_a_hit():
    """Otherwise a caller stuck retrying past the limit would never recover even
    after the window rolls forward -- each rejected call would keep pushing a fresh
    hit onto the window."""
    limiter, clock = _limiter(max_requests=1, window_seconds=60)
    limiter.enforce("1.2.3.4")

    with pytest.raises(HTTPException):
        limiter.enforce("1.2.3.4")
    with pytest.raises(HTTPException):
        limiter.enforce("1.2.3.4")

    clock.now = 61.0  # past the first (only) real hit's window
    limiter.enforce("1.2.3.4")  # allowed again -- no lingering effect from the rejections


def test_a_hit_outside_the_window_is_forgotten():
    limiter, clock = _limiter(max_requests=1, window_seconds=60)
    limiter.enforce("1.2.3.4")

    clock.now = 60.1
    limiter.enforce("1.2.3.4")  # the first hit has aged out of the 60s window
