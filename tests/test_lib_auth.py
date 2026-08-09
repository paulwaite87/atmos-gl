#!/usr/bin/env python3
"""ADMIN_EMAILS allowlist parsing (issue #303)."""
from unittest.mock import patch

from atmos_gl.lib.auth import is_admin_email


def test_no_admin_emails_configured_means_nobody_is_admin():
    with patch.dict("os.environ", {}, clear=True):
        assert is_admin_email("anyone@example.com") is False


def test_empty_admin_emails_means_nobody_is_admin():
    with patch.dict("os.environ", {"ADMIN_EMAILS": ""}):
        assert is_admin_email("anyone@example.com") is False


def test_exact_match_is_admin():
    with patch.dict("os.environ", {"ADMIN_EMAILS": "paul@example.com"}):
        assert is_admin_email("paul@example.com") is True


def test_non_member_is_not_admin():
    with patch.dict("os.environ", {"ADMIN_EMAILS": "paul@example.com"}):
        assert is_admin_email("someone-else@example.com") is False


def test_comma_separated_list_with_whitespace():
    with patch.dict(
        "os.environ", {"ADMIN_EMAILS": "paul@example.com, ops@example.com , third@example.com"}
    ):
        assert is_admin_email("ops@example.com") is True
        assert is_admin_email("third@example.com") is True


def test_matching_is_case_insensitive():
    with patch.dict("os.environ", {"ADMIN_EMAILS": "Paul@Example.com"}):
        assert is_admin_email("paul@example.com") is True
        assert is_admin_email("PAUL@EXAMPLE.COM") is True
