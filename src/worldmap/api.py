#!/usr/bin/env python3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Import the new decoupled router files
from worldmap.routes import (
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

app = FastAPI(title="WorldMap Configuration API")

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

# Keep the static asset pipeline mounted at root
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
app.include_router(tiles.router)
app.include_router(markers.router)
app.include_router(backfill.router)
app.include_router(status.router)


@app.get("/")
def health_check():
    return {"status": "online", "message": "WorldMap Engine routing operational."}
