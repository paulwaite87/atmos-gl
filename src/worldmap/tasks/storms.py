#!/usr/bin/env python3
import os
import logging
import gc
import requests
import numpy as np
import pandas as pd
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.image as mpimg
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from datetime import datetime, timedelta, timezone
import cartopy.crs as ccrs

# Internal library imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

logger = logging.getLogger(__name__)


class StormUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "storms", map_data)
        self.set_output_path()
        self.csv_path = os.path.join(self.workdir, "data/active_storms.csv")

    def get_active_csv_url(self):
        """Scrapes the NOAA IBTrACS directory for the live global 'ACTIVE' CSV archive."""
        directory_url = self.settings.get("ibtracs_url").strip()
        try:
            response = requests.get(directory_url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if "ACTIVE" in href.upper() and href.endswith(".csv"):
                    return directory_url.rstrip("/") + "/" + href
        except Exception as e:
            raise RuntimeError(f"Failed to scrape storms directory: {e}")
        return "No ACTIVE storms"

    def download_if_newer(self) -> bool:
        """Downloads the global IBTrACS CSV only if the remote file is newer than local cache."""
        try:
            active_url = self.get_active_csv_url()

            # If the planet is calm...
            if active_url == "No ACTIVE storms":
                logger.info("No ACTIVE storms currently on NOAA servers")
                return False

            response = requests.head(active_url, timeout=10)
            response.raise_for_status()

            remote_mtime_str = response.headers.get('Last-Modified')
            remote_mtime = None
            if remote_mtime_str:
                remote_mtime = datetime.strptime(remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z').replace(
                    tzinfo=timezone.utc)

            file_exists = os.path.exists(self.csv_path)
            if file_exists and remote_mtime:
                local_mtime = datetime.fromtimestamp(os.path.getmtime(self.csv_path), tz=timezone.utc)
                if remote_mtime <= local_mtime:
                    logger.info("Storms CSV cache is up to date.")
                    return False

            logger.info(f"Downloading fresh storms data from {active_url}")
            r = requests.get(active_url, timeout=30)
            r.raise_for_status()

            os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
            with open(self.csv_path, "wb") as f:
                f.write(r.content)
            return True
        except Exception as e:
            logger.warning(f"Download of storms data failed: {e}")
            return False

    def _parse_atcf_file(self, file_url, target_name):
        """Downloads and parses a generic ATCF .fst file, returning valid forecast tracks if matching target_name."""
        forecast_points = []
        try:
            file_text = requests.get(file_url, timeout=8).text
            lines = file_text.splitlines()
            if not lines:
                return []

            first_line_parts = lines[0].split(",")
            if len(first_line_parts) <= 27 or target_name not in first_line_parts[27].upper():
                return []

            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 10:
                    continue
                try:
                    tau = int(parts[5])
                    if tau == 0:
                        continue
                except ValueError:
                    continue

                raw_lat = parts[6]
                raw_lon = parts[7]
                if not raw_lat or not raw_lon:
                    continue

                lat_val = float(raw_lat[:-1]) * 0.1
                if raw_lat.endswith("S"): lat_val = -lat_val

                lon_val = float(raw_lon[:-1]) * 0.1
                if raw_lon.endswith("W"): lon_val = -lon_val

                forecast_points.append({
                    "TAU": tau,
                    "LAT": lat_val,
                    "LON": lon_val,
                    "TYPE": "FORECAST"
                })
        except Exception as e:
            logger.debug(f"Failed parsing storms track file {file_url}: {e}")
        return forecast_points

    def _fetch_global_forecast(self, storm_name):
        """Scrapes both JTWC and NHC active forecasting directories simultaneously."""
        target_name = storm_name.upper().strip()

        jtwc_endpoint = self.settings.get("jtwc_url").strip()
        nhc_endpoint = self.settings.get("nhc_url").strip()

        endpoints = [
            {"name": "JTWC", "url": jtwc_endpoint},
            {"name": "NHC", "url": nhc_endpoint}
        ]

        for source in endpoints:
            try:
                if not source["url"]:
                    continue

                response = requests.get(source["url"], timeout=10)
                if response.status_code != 200:
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                fst_files = [link["href"] for link in soup.find_all("a", href=True) if link["href"].endswith(".fst")]

                for fst_file in fst_files:
                    if fst_file.startswith("http"):
                        file_url = fst_file
                    else:
                        file_url = source["url"].rstrip("/") + "/" + fst_file

                    found_tracks = self._parse_atcf_file(file_url, target_name)
                    if found_tracks:
                        logger.info(f"Successfully linked official {source['name']} storm forecast track for {storm_name}")
                        df_fc = pd.DataFrame(found_tracks).drop_duplicates(subset=["TAU"]).sort_values("TAU")
                        return df_fc.to_dict(orient="records")

            except Exception as e:
                logger.warning(f"Failed polling live feed for storm {source['name']}: {e}")

        return []

    def _build_cone_polygons(self, future_track_df):
        """Calculates geographic error envelopes along a track array to draw the cone geometry."""
        left_points = []
        right_points = []

        for idx, (_, row) in enumerate(future_track_df.iterrows()):
            lat, lon = row["LAT"], row["LON"]
            tau = row.get("TAU", 24)
            r = 0.4 + (tau * 0.045)

            if idx < len(future_track_df) - 1:
                next_row = future_track_df.iloc[idx + 1]
                dlat = next_row["LAT"] - lat
                dlon = next_row["LON"] - lon
            else:
                prev_row = future_track_df.iloc[idx - 1]
                dlat = lat - prev_row["LAT"]
                dlon = lon - prev_row["LON"]

            heading = np.arctan2(dlat, dlon)

            left_lat = lat + r * np.sin(heading + np.pi / 2)
            left_lon = lon + r * np.cos(heading + np.pi / 2)
            right_lat = lat + r * np.sin(heading - np.pi / 2)
            right_lon = lon + r * np.cos(heading - np.pi / 2)

            left_points.append((left_lon, left_lat))
            right_points.append((right_lon, right_lat))

        right_points.reverse()
        return left_points + right_points

    def plot_storms(self, df):
        """Plots the global tracks, live forecast cones, and PNG symbols directly to the image canvas."""
        alpha_cone = self.settings.getfloat("cone_alpha", fallback=0.18)
        cone_color = self.settings.get("cone_color", fallback="white")
        track_color = self.settings.get("track_color", fallback="red")

        marker_zoom = self.settings.getfloat("marker_zoom", fallback=0.5)
        marker_path = self.settings.get("marker_image",
                                        fallback=os.path.join(self.workdir, "images", "storm_symbol.png"))

        storm_icon = None
        if os.path.exists(marker_path):
            try:
                storm_icon = mpimg.imread(marker_path)
            except Exception as e:
                logger.warning(f"Could not load custom storm icon: {e}")

        plot = Plot(self.map_data.region)
        plot.get_figure()

        for sid, storm_df in df.groupby("SID"):
            storm_df = storm_df.sort_values(["TYPE", "TAU" if "TAU" in storm_df.columns else "LAT"])
            storm_name = storm_df["NAME"].iloc[0]

            past_track = storm_df[storm_df["TYPE"] != "FORECAST"]
            future_track = storm_df[storm_df["TYPE"] != "PAST"]

            if len(future_track) >= 2:
                cone_vertices = self._build_cone_polygons(future_track)
                polygon_patch = patches.Polygon(
                    cone_vertices, closed=True, transform=ccrs.PlateCarree(),
                    facecolor=cone_color, edgecolor=cone_color, linewidth=1.0, linestyle="--",
                    alpha=alpha_cone, zorder=3
                )
                plot.ax.add_patch(polygon_patch)

            plot.ax.plot(
                past_track["LON"].values, past_track["LAT"].values,
                transform=ccrs.PlateCarree(), color=track_color, linewidth=2.0, linestyle="-", zorder=4
            )
            if len(future_track) > 1:
                plot.ax.plot(
                    future_track["LON"].values, future_track["LAT"].values,
                    transform=ccrs.PlateCarree(), color=track_color, linewidth=1.5, linestyle=":", zorder=4
                )

            current_pt = storm_df[storm_df["TYPE"] == "CURRENT"]
            if not current_pt.empty:
                curr_row = current_pt.iloc[0]
                lon_pt, lat_pt = curr_row["LON"], curr_row["LAT"]

                if storm_icon is not None:
                    mapped_coords = plot.ax.projection.transform_point(lon_pt, lat_pt, src_crs=ccrs.PlateCarree())
                    imagebox = OffsetImage(storm_icon, zoom=marker_zoom)
                    ab = AnnotationBbox(imagebox, (mapped_coords[0], mapped_coords[1]), frameon=False, zorder=6)
                    plot.ax.add_artist(ab)
                else:
                    plot.ax.plot(lon_pt, lat_pt, transform=ccrs.PlateCarree(), marker='o', color=track_color,
                                 markersize=7, zorder=5)

                plot.ax.text(
                    lon_pt + 0.5, lat_pt + 0.5, f"{storm_name}", transform=ccrs.PlateCarree(),
                    color="white", fontsize=10, weight="bold",
                    bbox=dict(facecolor='#111111', alpha=0.6, boxstyle='round,pad=0.2', edgecolor='none'),
                    zorder=7
                )

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, 'close', None)
        if callable(plt_close):
            plt_close()
        logger.info(f"Successfully rendered storms layer directly to {self.output_path}")

    def run(self):
        self.exit_if_disabled()

        data_updated = self.download_if_newer()
        marker_file_exists = os.path.exists(self.output_path)

        if not (data_updated or not marker_file_exists or self.config.has_changed):
            logger.info("Storms layer not updated.")
            return

        if not os.path.exists(self.csv_path):
            logger.error("No raw storms tracking dataset available.")
            return

        try:
            expiry_days = self.settings.getint("expiry_days", fallback=5)
            now = datetime.now(timezone.utc)

            raw_df = pd.read_csv(self.csv_path, header=0, low_memory=False, encoding="utf-8-sig")
            raw_df = raw_df[raw_df["SID"] != "SID"]

            raw_df["LAT"] = pd.to_numeric(raw_df["LAT"], errors="coerce")
            raw_df["LON"] = pd.to_numeric(raw_df["LON"], errors="coerce")
            raw_df["NAME"] = raw_df["NAME"].astype(str).str.strip()
            raw_df["ISO_TIME"] = pd.to_datetime(raw_df["ISO_TIME"], format="%Y-%m-%d %H:%M:%S",
                                                errors="coerce").dt.tz_localize("UTC")

            latest_times = raw_df.groupby("SID")["ISO_TIME"].transform("max")
            raw_df = raw_df[(now - latest_times) <= timedelta(days=expiry_days)].copy()

            if raw_df.empty:
                if os.path.exists(self.output_path):
                    open(self.output_path, 'w').close()
                return

            processed_data = []
            for sid, group in raw_df.groupby("SID"):
                group = group.sort_values("ISO_TIME")
                total_pts = len(group)
                storm_name = group["NAME"].iloc[0]

                for idx, (_, row) in enumerate(group.iterrows()):
                    pt_type = "PAST"
                    if idx == total_pts - 1:
                        pt_type = "CURRENT"
                    processed_data.append({
                        "SID": sid, "NAME": storm_name, "LAT": row["LAT"], "LON": row["LON"],
                        "TYPE": pt_type, "TAU": 0
                    })

                global_forecasts = self._fetch_global_forecast(storm_name)
                if not global_forecasts:
                    last_row = group.iloc[-1]
                    global_forecasts = [{"TAU": 0, "LAT": last_row["LAT"], "LON": last_row["LON"], "TYPE": "CURRENT"}]

                for fc in global_forecasts:
                    processed_data.append({
                        "SID": sid, "NAME": storm_name, "LAT": fc["LAT"], "LON": fc["LON"],
                        "TYPE": fc["TYPE"], "TAU": fc["TAU"]
                    })

            df = pd.DataFrame(processed_data)
            self.plot_storms(df)

        except Exception as e:
            logger.exception(f"Storms tracking execution crash: {e}")