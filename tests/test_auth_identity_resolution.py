#!/usr/bin/env python3
"""Unit tests for the pure per-provider identity-resolution helpers in routes/auth.py
(issue #306) -- these parse an already-fetched OIDC userinfo dict (Google) or GitHub's
raw /user + /user/emails JSON into a uniform (email, verified, name, subject) tuple.
Kept separate from the actual OAuth token exchange/HTTP fetch, which stays untested per
this repo's existing convention (see test_auth_route.py's docstring) -- these are the
part of that flow that's genuinely a pure function over already-fetched data.
"""
from atmos_gl.routes.auth import _github_identity, _google_identity, _primary_github_email


# --- _google_identity ---


def test_google_identity_extracts_verified_userinfo():
    userinfo = {
        "email": "visitor@example.com", "email_verified": True,
        "name": "Visitor", "sub": "google-sub-1",
    }

    assert _google_identity(userinfo) == ("visitor@example.com", True, "Visitor", "google-sub-1")


def test_google_identity_reports_unverified_when_the_claim_is_false():
    userinfo = {"email": "visitor@example.com", "email_verified": False, "name": "Visitor", "sub": "google-sub-1"}

    email, verified, name, subject = _google_identity(userinfo)

    assert verified is False


def test_google_identity_handles_a_missing_userinfo_entirely():
    assert _google_identity({}) == (None, False, None, None)


# --- _primary_github_email ---


def test_primary_github_email_picks_the_entry_flagged_primary():
    emails = [
        {"email": "secondary@example.com", "primary": False, "verified": True},
        {"email": "primary@example.com", "primary": True, "verified": True},
    ]

    assert _primary_github_email(emails) == {"email": "primary@example.com", "primary": True, "verified": True}


def test_primary_github_email_returns_none_for_an_empty_list():
    assert _primary_github_email([]) is None


def test_primary_github_email_returns_none_when_nothing_is_flagged_primary():
    emails = [{"email": "only@example.com", "primary": False, "verified": True}]

    assert _primary_github_email(emails) is None


# --- _github_identity ---


def test_github_identity_uses_the_primary_verified_email():
    profile = {"id": 12345, "name": "Visitor", "login": "visitor-gh"}
    emails = [{"email": "primary@example.com", "primary": True, "verified": True}]

    assert _github_identity(profile, emails) == ("primary@example.com", True, "Visitor", "12345")


def test_github_identity_falls_back_to_login_when_name_is_absent():
    """GitHub's profile name is optional (a user can leave it blank) -- login is
    always present, matching how a visitor identifies themselves on GitHub itself."""
    profile = {"id": 12345, "name": None, "login": "visitor-gh"}
    emails = [{"email": "primary@example.com", "primary": True, "verified": True}]

    _, _, name, _ = _github_identity(profile, emails)

    assert name == "visitor-gh"


def test_github_identity_reports_unverified_when_the_primary_email_is_not_verified():
    """Distinguishes "an email exists but isn't verified" from "no email at all" --
    same two-case shape as _google_identity's email_verified check, just sourced from
    a separate REST call instead of one ID token claim."""
    profile = {"id": 12345, "name": "Visitor", "login": "visitor-gh"}
    emails = [{"email": "primary@example.com", "primary": True, "verified": False}]

    email, verified, name, subject = _github_identity(profile, emails)

    assert email == "primary@example.com"
    assert verified is False


def test_github_identity_reports_no_email_when_no_primary_email_exists():
    profile = {"id": 12345, "name": "Visitor", "login": "visitor-gh"}

    email, verified, name, subject = _github_identity(profile, emails=[])

    assert email is None
    assert verified is False
    assert subject == "12345"


def test_github_identity_stringifies_the_numeric_profile_id():
    """provider_user_id is a String(255) column (db/models.py's UserIdentity) --
    GitHub's numeric `id` must be coerced, not stored as an int."""
    profile = {"id": 999, "name": "Visitor", "login": "visitor-gh"}
    emails = [{"email": "primary@example.com", "primary": True, "verified": True}]

    _, _, _, subject = _github_identity(profile, emails)

    assert subject == "999"
    assert isinstance(subject, str)
