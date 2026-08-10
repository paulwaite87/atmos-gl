#!/usr/bin/env python3
"""Google sign-in (issue #303): OAuth redirect/callback, logout, and a /me endpoint
the frontend polls to know whether -- and as whom -- the visitor is signed in."""
import logging
import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from atmos_gl.db.user_adapter import UserAdapter
from atmos_gl.lib.auth import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS

logger = logging.getLogger("atmos_gl.routes.auth")

router = APIRouter(prefix="/api/auth", tags=["Auth"])

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    client_kwargs={"scope": "openid email profile"},
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


@router.get("/login/google")
async def login_google(request: Request):
    redirect_uri = request.url_for("callback_google")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback/google")
async def callback_google(
    request: Request, user_adapter: UserAdapter = Depends(get_user_adapter),
):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        logger.warning(f"Google OAuth callback failed: {e}")
        raise HTTPException(status_code=400, detail="Google sign-in failed")

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    subject = userinfo.get("sub")
    if not email or not subject:
        raise HTTPException(status_code=400, detail="Google did not provide an email")
    if not userinfo.get("email_verified"):
        # Google-side unverified addresses must never reach get_or_create_user: it links
        # accounts across providers by email match, so an unverified address could hijack
        # an existing user's identity (and any admin recognition tied to that email).
        raise HTTPException(status_code=400, detail="Google email is not verified")

    user = user_adapter.get_or_create_user(
        email=email, name=userinfo.get("name"), provider="google", provider_user_id=subject,
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
