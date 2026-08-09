#!/usr/bin/env python3
"""Admin recognition for signed-in users (issue #303). ADMIN_EMAILS is a
comma-separated allowlist env var, read directly (not through AtmosGLConfig -- it
must never be exposed via GET /api/config, which is public and unauthenticated)."""
import os

# Shared by UserAdapter.create_session's DB expiry and the session cookie's max-age
# (routes/auth.py) so the two can't drift apart. 30 days.
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30

# Name of the app's own long-lived, DB-backed session cookie -- distinct from
# Starlette SessionMiddleware's "session" cookie, which is only used transiently to
# hold OAuth state/nonce during the Google sign-in redirect round-trip.
SESSION_COOKIE_NAME = "atmos_session"


def admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin_email(email: str) -> bool:
    return email.strip().lower() in admin_emails()
