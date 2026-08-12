#!/usr/bin/env python3
"""GET /api/version -- the release/build identifier actually running, baked into the
image at CI build time (see .github/workflows/docker-publish.yml) from the git tag or
commit used for that build, via the Dockerfile's APP_VERSION build arg. Unauthenticated,
like /api/config -- the account menu's version footer needs it regardless of sign-in
state."""
import os

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["Version"])


@router.get("/version")
def get_version():
    return {"version": os.getenv("APP_VERSION", "unknown")}
