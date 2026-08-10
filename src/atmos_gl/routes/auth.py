#!/usr/bin/env python3
"""Google + GitHub sign-in (issues #303, #306): OAuth redirect/callback, logout, and a
/me endpoint the frontend polls to know whether -- and as whom -- the visitor is signed
in. /login/{provider} and /callback/{provider} are shared, parameterized routes rather
than one route pair per provider -- the account-linking safety logic (the unverified-
email gate below) lives once, not once per provider, so it can't drift between them.
Only "how do I get (email, verified, name, subject) out of this provider's response" is
genuinely provider-specific: Google is OIDC (one ID-token claim set via authlib's
`userinfo`), GitHub is not (a separate REST profile + email-list fetch)."""
import logging
import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from atmos_gl.db.user_adapter import UserAdapter
from atmos_gl.lib.auth import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS
from atmos_gl.lib.rate_limit import RateLimiter, rate_limiter_dependency

logger = logging.getLogger("atmos_gl.routes.auth")

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# Shared by /login/{provider} and /callback/{provider} (issue #307) -- an attacker
# hammering either counts against the same per-IP budget, since both are part of the
# same sign-in flow. Keyed by IP, not user, since there's no session yet at this point.
# 20 requests / 5 minutes: generous for legitimate retries/multiple tabs, tight enough
# to block a scripted hammering loop.
get_login_rate_limiter = rate_limiter_dependency(max_requests=20, window_seconds=300)


def _enforce_login_rate_limit(request: Request, limiter: RateLimiter = Depends(get_login_rate_limiter)) -> None:
    ip = request.client.host if request.client else "unknown"
    limiter.enforce(ip)

# Display names for error messages -- "github".title() would render "Github", not
# "GitHub", so this can't just be provider.title().
_DISPLAY_NAMES = {"google": "Google", "github": "GitHub"}

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    client_kwargs={"scope": "openid email profile"},
)
oauth.register(
    name="github",
    # GitHub isn't OIDC -- no discovery document, no userinfo/ID-token claims. Profile
    # and email come from separate REST calls below (see _github_identity).
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_id=os.getenv("GITHUB_CLIENT_ID"),
    client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
    # user:email is needed on top of the implicit read:user profile access -- a
    # GitHub user's email can be private and is omitted from /user entirely in that
    # case, only reachable via /user/emails with this scope granted.
    client_kwargs={"scope": "read:user user:email"},
)


def get_user_adapter() -> UserAdapter:
    return UserAdapter()


def current_user_optional(request: Request, user_adapter: UserAdapter) -> dict | None:
    """None if not signed in (no cookie or an expired/unknown session), the user dict
    otherwise -- the shared primitive require_admin/require_login build their 401s on
    top of, and also used directly (not as a Depends) by GET /api/config (routes/
    config.py) to merge a signed-in user's personal overrides in when present, while
    staying fully unauthenticated (no 401) for anyone else."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return user_adapter.get_session_user(token) if token else None


def require_admin(request: Request, user_adapter: UserAdapter = Depends(get_user_adapter)) -> dict:
    """FastAPI dependency gating admin-only routes (issue #304): 401 with no/invalid
    session, 403 if signed in but not an admin. Reused via Depends(require_admin) by
    routes/config.py, routes/status.py, and routes/system_status.py -- every route
    that views or changes the global configuration or the internal operational status
    only the config UI shows. GET /api/config and /api/forecast_state stay
    unauthenticated: the public map depends on them to load (see #304's acceptance
    criteria)."""
    user = current_user_optional(request, user_adapter)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_login(request: Request, user_adapter: UserAdapter = Depends(get_user_adapter)) -> dict:
    """FastAPI dependency gating signed-in-only routes (issue #305/#314): 401 with
    no/invalid session, no admin check -- lighter than require_admin, for routes any
    signed-in visitor may use (e.g. their own personal settings), not just admins."""
    user = current_user_optional(request, user_adapter)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _google_identity(userinfo: dict) -> tuple[str | None, bool, str | None, str | None]:
    return (
        userinfo.get("email"),
        bool(userinfo.get("email_verified")),
        userinfo.get("name"),
        userinfo.get("sub"),
    )


def _primary_github_email(emails: list[dict]) -> dict | None:
    """GitHub's GET /user/emails always includes exactly one primary=true entry (once
    the account has any verified email) -- picked here without pre-filtering on
    `verified`, so the caller can distinguish "no email at all" from "an email exists
    but isn't verified", the same two cases _google_identity's email_verified claim
    already distinguishes."""
    return next((e for e in emails if e.get("primary")), None)


def _github_identity(profile: dict, emails: list[dict]) -> tuple[str | None, bool, str | None, str | None]:
    subject = str(profile["id"]) if profile.get("id") is not None else None
    # login is always present; name is an optional profile field a user can leave blank.
    name = profile.get("name") or profile.get("login")
    primary = _primary_github_email(emails)
    if primary is None:
        return None, False, name, subject
    return primary.get("email"), bool(primary.get("verified")), name, subject


async def _resolve_github_identity(client, token) -> tuple[str | None, bool, str | None, str | None]:
    profile_resp = await client.get("user", token=token)
    profile_resp.raise_for_status()
    emails_resp = await client.get("user/emails", token=token)
    # A 403 here means the user granted sign-in but not the user:email scope (or
    # revoked it) -- treated the same as "no verified email", not a hard failure.
    emails = emails_resp.json() if emails_resp.status_code == 200 else []
    return _github_identity(profile_resp.json(), emails)


_PROVIDERS = ("google", "github")


@router.get("/login/{provider}")
async def login(provider: str, request: Request, _: None = Depends(_enforce_login_rate_limit)):
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown sign-in provider")
    client = oauth.create_client(provider)
    redirect_uri = request.url_for("callback", provider=provider)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/callback/{provider}")
async def callback(
    provider: str,
    request: Request,
    user_adapter: UserAdapter = Depends(get_user_adapter),
    _: None = Depends(_enforce_login_rate_limit),
):
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown sign-in provider")
    display_name = _DISPLAY_NAMES[provider]
    client = oauth.create_client(provider)

    try:
        token = await client.authorize_access_token(request)
        if provider == "google":
            email, verified, name, subject = _google_identity(token.get("userinfo") or {})
        else:
            # GitHub's identity fetch is a live REST call (see _resolve_github_identity),
            # unlike Google's claims already sitting in `token` -- a hiccup here (an
            # expired token, a GitHub 5xx, a rate limit) must fail the same clean way as
            # a token-exchange failure above, not surface as an unhandled 500.
            email, verified, name, subject = await _resolve_github_identity(client, token)
    except Exception as e:
        logger.warning(f"{display_name} OAuth callback failed: {e}")
        raise HTTPException(status_code=400, detail=f"{display_name} sign-in failed")

    if not email or not subject:
        raise HTTPException(status_code=400, detail=f"{display_name} did not provide an email")
    if not verified:
        # An unverified address must never reach get_or_create_user: it links accounts
        # across providers by email match, so an unverified address could hijack an
        # existing user's identity (and any admin recognition tied to that email).
        raise HTTPException(status_code=400, detail=f"{display_name} email is not verified")

    user = user_adapter.get_or_create_user(
        email=email, name=name, provider=provider, provider_user_id=subject,
    )
    session_token = user_adapter.create_session(user["id"], ttl_seconds=SESSION_TTL_SECONDS)

    response = RedirectResponse(url="/")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # TODO: flip to True once TLS is terminated in front of this deployment --
        # there's no HTTPS anywhere in this stack yet (see config/nginx.conf).
        secure=False,
    )
    return response


@router.post("/logout")
async def logout(
    request: Request, response: Response, user_adapter: UserAdapter = Depends(get_user_adapter),
):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        user_adapter.delete_session(token)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "success"}


@router.get("/me")
async def me(request: Request, user_adapter: UserAdapter = Depends(get_user_adapter)):
    user = current_user_optional(request, user_adapter)
    if user is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "email": user["email"],
        "name": user["name"],
        "is_admin": user["is_admin"],
    }
