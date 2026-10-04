#!/usr/bin/env python3
"""Keeps the Population Density layer's people-per-km² grid (lib/population_density.py)
current from the JRC's GHS-POP GeoTIFF.

The source is a modelled epoch that changes only when the JRC republishes the file
(or data_collector.datasources.population_density is pointed at a newer release), so
has_new_data() is one HEAD request compared against the marker the cached grid was
built from. A new file is fetched in parallel byte ranges across as many cycles as it
takes -- each collect() stops starting new chunks after _DOWNLOAD_BUDGET_S, keeping
the data_collector's heartbeat clear of the Data Status page's dead threshold, same
reasoning as FloodRiskHistoricalCollector's per-cycle tile budget. Once every chunk
is on disk, the same cycle sums the ~1km grid onto 0.05deg cells, writes the density
grid and discards the download.
"""
import logging
import os

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.lib.data_status import build_status, estimate_next_update, read_process_status
from atmos_gl.lib.population_density import (
    GHSL_POP_PAGE_URL,
    GHSL_POP_URL,
    _download_root,
    assemble_zip,
    cached_source,
    chunk_progress,
    density_from_zip,
    density_grid_cache_path,
    discard_download,
    download_chunks,
    is_current,
    remote_file_info,
    save_cached_source,
    save_density_grid,
)

logger = logging.getLogger(__name__)


class PopulationDensityCollector(CollectorBase):
    section = "population_density"
    channel_key = "population_density"
    datasource_key = "population_density"
    display_label = "GHSL Population Grid"

    _DOWNLOAD_BUDGET_S = 240

    def _url(self) -> str:
        return self.datasource_url(self.datasource_key) or GHSL_POP_URL

    def source_url(self) -> str | None:
        """Overridden: the dataset's landing page, not the configured 484MB zip."""
        return GHSL_POP_PAGE_URL

    def _download_in_progress(self) -> bool:
        root = _download_root()
        return os.path.isdir(root) and any(os.scandir(root))

    def is_stale(self, last_run: float | None) -> bool:
        """A part-finished download resumes every data_collector cycle rather than
        waiting out runs_per_day."""
        return self._download_in_progress() or super().is_stale(last_run)

    def has_new_data(self) -> bool:
        """Stashes the HEAD result on self._remote for collect(), same contract as
        VegetationMaskCollector.has_new_data(); a failed check skips this cycle."""
        try:
            self._remote = remote_file_info(self._url())
        except Exception as e:
            logger.debug(f"{self.section}: HEAD failed ({e}); will retry.")
            self._remote = None
            return False
        return not is_current(self.workdir, self._remote)

    def collect(self) -> None:
        remote = getattr(self, "_remote", None)
        if remote is None:
            try:
                remote = remote_file_info(self._url())
            except Exception as e:
                logger.warning(f"{self.section}: HEAD failed ({e}); skipping.")
                return
        if is_current(self.workdir, remote):
            return

        if not download_chunks(remote, self._DOWNLOAD_BUDGET_S, on_progress=self._record_progress):
            done, total = chunk_progress(remote)
            logger.info(f"{self.section}: {done}/{total} chunks downloaded; resuming next cycle.")
            return

        self._record_progress(*chunk_progress(remote))
        try:
            density, lat, lon = density_from_zip(assemble_zip(remote))
        except Exception:
            # A corrupt chunk would fail the same way every cycle -- start over.
            discard_download(remote)
            raise
        save_density_grid(density_grid_cache_path(self.workdir), density, lat, lon)
        save_cached_source(self.workdir, remote)
        discard_download(remote)
        logger.info(f"{self.section}: density grid built from {remote['url']}.")

    def _record_progress(self, done: int, total: int) -> None:
        self.process_status_adapter.record_progress(self.section, "collector", done, total)

    def data_status(self) -> dict:
        """Coverage-based, like FloodRiskHistoricalCollector.data_status(): 100 once a
        grid is cached, else the share of the download done. The download itself lives
        in data_collector's own container, out of map_api's sight, so collect()
        records its progress on this collector's process_status row (record_progress,
        as AircraftCollector does). next_update is the real daily check, shown whether
        or not the layer is: collection doesn't depend on `enabled`."""
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.section
        )
        row = self.process_status_adapter.get_process_status(self.section) or {}
        done, total = row.get("progress_current") or 0, row.get("progress_total") or 0
        if os.path.exists(density_grid_cache_path(self.workdir)):
            percent = 100.0
            source = (cached_source(self.workdir) or {}).get("url", "")
            detail = last_error or f"grid cached ({os.path.basename(source)})"
        elif total and done >= total:
            percent = 99.0
            detail = last_error or "downloaded; building the density grid"
        elif total:
            percent = 100.0 * done / total
            detail = last_error or f"downloading: {done}/{total} pieces of ~484MB"
        else:
            percent = 0.0
            detail = last_error or "not yet downloaded (~484MB)"
        return build_status(
            name=self.section,
            kind="collector",
            percent=percent,
            last_updated=last_updated,
            next_update=estimate_next_update(last_updated, self.period_s, True),
            enabled=self.enabled,
            detail=detail,
            status=status,
        )
