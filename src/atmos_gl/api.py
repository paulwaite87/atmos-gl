#!/usr/bin/env python3
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Import the new decoupled router files
from atmos_gl.routes import (
    satellites,
    storms,
    volcanoes,
    quakes,
    lightning,
    shipping,
    config,
    terminator,
    tiles,
    backfill,
    markers,
    status,
)

app = FastAPI(title="Atmos GL Configuration API")

origins = [
    "http://localhost:8180",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keep the static asset pipeline mounted at root. Docker's bind mount
# (./data:/opt/project/data) auto-creates this directory at container start, but a bare
# `uv run pytest`/uvicorn invocation (e.g. CI) has no such mount -- StaticFiles requires
# the directory to exist at import time, so ensure it does rather than crash on import.
os.makedirs("data", exist_ok=True)
app.mount("/data", StaticFiles(directory="data"), name="data")

# -------------------------------------------------------------
# ROUTER HOOKS - Registering the modular layout blocks
# -------------------------------------------------------------
app.include_router(terminator.router)
app.include_router(satellites.router)
app.include_router(storms.router)
app.include_router(volcanoes.router)
app.include_router(quakes.router)
app.include_router(lightning.router)
app.include_router(shipping.router)
app.include_router(config.router)
app.include_router(config.ui_router)
app.include_router(tiles.router)
app.include_router(markers.router)
app.include_router(backfill.router)
app.include_router(status.router)


@app.get("/")
def health_check():
    return {"status": "online", "message": "Atmos GL Engine routing operational."}
