#!/usr/bin/env python3
"""Demand-driven backfill request endpoint.

When a frontend layer requests a per-hour field PNG (e.g. currents_f000_data.png) and
gets a 404, it POSTs the missing (product, date, run, hour) here. We enqueue it in the
backfill_requests table; the data_collector drains the queue on its fast poll, fetches
the field, and the layer task renders the PNG. This endpoint only enqueues — it never
fetches synchronously (the request must return immediately, and the collector owns all
upstream I/O)."""

import re
import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from worldmap.lib.db import Database

logger = logging.getLogger("worldmap.routes.backfill")

router = APIRouter(prefix="/api", tags=["Backfill"])

# Only products the pipeline actually knows how to fetch may be enqueued, so a bad or
# malicious client can't fill the queue with junk. Keep in sync with the collector's
# per-product handlers (atmos products, currents, waves).
ALLOWED_PRODUCTS = {
    "wind",
    "isobars",
    "precipitation",
    "temperature",
    "ozone",
    "stormwatch",
    "currents",
    "waves",
}

_RUN_RE = re.compile(r"^(00|06|12|18)$")
_DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")


class BackfillRequest(BaseModel):
    product: str = Field(..., max_length=32)
    date: str = Field(..., description="GFS date, YYYYMMDD or YYYY-MM-DD")
    run: str = Field(..., description="GFS run hour: 00/06/12/18")
    hour: int = Field(..., ge=0, le=384, description="forecast hour")


@router.post("/request_backfill")
async def request_backfill(req: BackfillRequest):
    """Enqueue a missing-field request. Idempotent and fast — returns as soon as the
    row is recorded; the collector does the actual work asynchronously."""
    product = req.product.strip().lower()
    if product not in ALLOWED_PRODUCTS:
        raise HTTPException(status_code=400, detail=f"unknown product '{product}'")
    if not _RUN_RE.match(req.run):
        raise HTTPException(status_code=400, detail="run must be 00, 06, 12 or 18")
    if not _DATE_RE.match(req.date):
        raise HTTPException(status_code=400, detail="date must be YYYYMMDD")

    # Normalise date to the stored form (the catalog uses a DATE column; psycopg2
    # accepts 'YYYY-MM-DD'). Accept the compact YYYYMMDD the frontend derives from runs.
    d = req.date.replace("-", "")
    iso_date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    try:
        date.fromisoformat(iso_date)  # validate it's a real calendar date
    except ValueError:
        raise HTTPException(status_code=400, detail="date is not a valid calendar date")

    try:
        db = Database()
        db.ensure_backfill_table()
        db.enqueue_backfill(iso_date, req.run, int(req.hour), product)
    except Exception as e:
        logger.error(f"request_backfill failed: {e}")
        raise HTTPException(status_code=500, detail="could not enqueue request")

    return {
        "status": "queued",
        "request": {
            "product": product,
            "date": iso_date,
            "run": req.run,
            "hour": int(req.hour),
        },
    }
