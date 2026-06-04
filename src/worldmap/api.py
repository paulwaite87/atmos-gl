#!/usr/bin/env python3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Import the new decoupled router files
from worldmap.routes import volcanoes, quakes, lightning, shipping, config

app = FastAPI(title="WorldMap Configuration API")

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
app.include_router(volcanoes.router)
app.include_router(quakes.router)
app.include_router(lightning.router)
app.include_router(shipping.router)
app.include_router(config.router)


@app.get("/")
def health_check():
    return {"status": "online", "message": "WorldMap Engine routing operational."}
