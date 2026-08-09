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


def _current_user(request: Request, user_adapter: UserAdapter) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return user_adapter.get_session_user(token) if token else None


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
    user = _current_user(request, user_adapter)
    if user is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "email": user["email"],
        "name": user["name"],
        "is_admin": user["is_admin"],
    }
