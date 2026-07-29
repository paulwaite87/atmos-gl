#!/usr/bin/env python3
"""Shared Copernicus CDS/ADS submit-then-poll retrieval mechanics, extracted from
collectors/greenhouse_gases.py so a second CDS-backed collector (air_quality) doesn't
have to re-implement or copy-paste this a second time -- credential resolution, a
bounded-timeout wrapper around cdsapi.Client.retrieve()'s otherwise-unbounded blocking
call, and unpacking the data_format=netcdf_zip archive every CDS dataset in this app
delivers.
"""
import concurrent.futures
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def resolve_cds_credentials(datasource_url_fn, label: str):
    """(base_url, api_key) for a CDS API request, or None (having logged why) if
    either CDSAPI_KEY or the cams_ads datasource isn't configured. Shared by every
    CDS-backed collector so none of them duplicates this check."""
    api_key = os.environ.get("CDSAPI_KEY", "").strip()
    if not api_key:
        logger.warning(f"{label}: no CDSAPI_KEY configured; skipping.")
        return None
    base_url = datasource_url_fn("cams_ads")
    if not base_url:
        logger.warning(f"{label}: no 'cams_ads' datasource configured; skipping.")
        return None
    return base_url, api_key


def retrieve_with_timeout(client, dataset: str, request: dict, target: str, timeout_s: float):
    """Run client.retrieve() (cdsapi's own blocking submit-then-poll-then-download) in
    a worker thread, bounded by timeout_s. Raises concurrent.futures.TimeoutError if
    the job doesn't finish in time -- the calling thread stops waiting, but the
    worker thread (and the in-flight CDS job) is not cancelled; a future cycle's
    collect() will find the cache still missing and request again.

    Deliberately NOT `with ThreadPoolExecutor(...) as pool:` -- confirmed live (a real
    request that should have timed out at 300s was still blocking the calling thread
    12+ minutes later) that a context-managed pool's __exit__ always calls
    shutdown(wait=True), which re-blocks until the still-running worker thread
    finishes regardless of how the `with` block was exited (even via this function's
    own TimeoutError) -- silently defeating the entire point of the bounded timeout.
    shutdown(wait=False) here actually releases the calling thread at timeout_s."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(client.retrieve, dataset, request, target)
    try:
        future.result(timeout=timeout_s)
    finally:
        pool.shutdown(wait=False)


def retrieve_and_unzip(
    client, dataset: str, request: dict, cache_dest: str, timeout_s: float, label: str
):
    """Submit `request` (bounded by timeout_s), then unzip the delivered
    data_format=netcdf_zip archive and move its single .nc member to cache_dest.
    Raises concurrent.futures.TimeoutError (bounded-timeout) or RuntimeError (archive
    had no .nc member) -- callers decide how to log/handle each."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = os.path.join(tmp_dir, "download.zip")
        retrieve_with_timeout(client, dataset, request, zip_path, timeout_s)

        with zipfile.ZipFile(zip_path) as zf:
            nc_names = [n for n in zf.namelist() if n.endswith(".nc")]
            if not nc_names:
                raise RuntimeError(f"{label}: no .nc file in downloaded archive")
            extracted_path = zf.extract(nc_names[0], tmp_dir)

        os.makedirs(os.path.dirname(cache_dest), exist_ok=True)
        tmp_dest = f"{cache_dest}.tmp"
        shutil.move(extracted_path, tmp_dest)
        os.replace(tmp_dest, cache_dest)


def retrieve_with_day_fallback(
    client, dataset: str, build_request, dest: str, timeout_s: float,
    search_days: int, label: str,
) -> bool:
    """Search backward from today for the newest published forecast run, retrying
    retrieve_and_unzip() once per day back to `search_days` days ago. `build_request`
    is a (date_str) -> request dict callable (e.g. build_cams_forecast_request).
    Returns True once a fetch succeeds (already cached at `dest`), False if every date
    in the window failed or a queued job timed out (both cases already logged; the
    caller has nothing further to do either way).

    Shared by every CDS-backed forecast collector that needs this "today's run isn't
    always published yet" fallback (CamsGhgForecastCollector, AirQualityCollector) --
    same day-search fallback shape resolve_gfs_baseline() (lib/gfs.py) uses for GFS's
    own publish lag."""
    last_error = None
    for day_offset in range(search_days):
        date_str = (
            datetime.now(timezone.utc) - timedelta(days=day_offset)
        ).strftime("%Y-%m-%d")
        request = build_request(date_str)

        try:
            retrieve_and_unzip(client, dataset, request, dest, timeout_s, label)
            logger.info(f"{label}: cached {date_str} -> {os.path.basename(dest)}")
            return True
        except concurrent.futures.TimeoutError:
            # A queued (not immediately rejected) job -- today's run does exist, it's
            # just slow. Don't also hammer earlier dates while it's pending.
            logger.warning(
                f"{label}: request for {date_str} timed out after {timeout_s}s; "
                f"will retry next cycle."
            )
            return False
        except Exception as e:
            last_error = e
            logger.debug(
                f"{label}: {date_str} not available yet ({e}); trying an earlier date."
            )

    logger.error(f"{label}: no run available in the last {search_days} day(s): {last_error}")
    return False
