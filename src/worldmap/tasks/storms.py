#!/usr/bin/env python3
import os
import logging
import requests
import numpy as np
import pandas as pd
from bs4 import BeautifulSoup
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
        # We no longer need the massive IBTrACS CSV cache

    def _get_file_list(self, directory_url):
        """Scrapes a generic HTTP directory for file links."""
        try:
            r = requests.get(directory_url, timeout=10)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            return [
                link["href"]
                for link in soup.find_all("a", href=True)
                if link["href"].endswith((".dat", ".fst"))
            ]
        except Exception as e:
            logger.debug(f"Failed to list directory {directory_url}: {e}")
            return []

    def _parse_latlon(self, lat_str, lon_str):
        """Converts ATCF lat/lon strings (e.g., '145N', '0805W') to floats."""
        lat_val = float(lat_str[:-1]) * 0.1
        if lat_str.endswith("S"):
            lat_val = -lat_val

        lon_val = float(lon_str[:-1]) * 0.1
        if lon_str.endswith("W"):
            lon_val = -lon_val
        return lat_val, lon_val

    def _parse_b_deck(self, url, now_utc, expiry_days):
        """Parses an ATCF b-deck (Best Track) and returns past/current points if active."""
        try:
            text = requests.get(url, timeout=10).text
            lines = text.splitlines()
            pts = []
            storm_name = None

            # Extract SID from filename (e.g., bsh122026.dat -> SH122026)
            filename = url.split("/")[-1]
            sid = filename[1:9].upper()

            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 10:
                    continue

                # Filter for Best Track lines
                if parts[4] == "BEST":
                    dt_str = parts[2]  # YYYYMMDDHH
                    dt = datetime.strptime(dt_str, "%Y%m%d%H").replace(
                        tzinfo=timezone.utc
                    )
                    lat, lon = self._parse_latlon(parts[6], parts[7])

                    # ATCF puts the storm name in column 27, if it exists
                    if len(parts) > 27 and parts[27]:
                        name = parts[27]
                        if name not in ["NONAME", "INVEST", "DB", "LO", "EX"]:
                            storm_name = name

                    pts.append(
                        {
                            "SID": sid,
                            "NAME": storm_name or sid,
                            "LAT": lat,
                            "LON": lon,
                            "TIME": dt,
                            "TYPE": "PAST",
                            "TAU": 0,
                        }
                    )

            if not pts:
                return []

            # Propagate the most accurate name found to all points
            final_name = storm_name or sid
            for p in pts:
                p["NAME"] = final_name

            # Enforce the Expiry Window
            latest_time = pts[-1]["TIME"]
            if (now_utc - latest_time) > timedelta(days=expiry_days):
                return []  # Storm is expired/dead

            # Mark the very last known point as CURRENT
            pts[-1]["TYPE"] = "CURRENT"
            return pts

        except Exception as e:
            logger.debug(f"Failed to parse B-deck {url}: {e}")
            return []

    def _parse_a_deck(self, url, sid):
        """Parses an ATCF a-deck/fst file and returns the latest official forecast track."""
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                return []

            lines = r.text.splitlines()
            valid_lines = []

            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 10:
                    continue
                # We only want the official forecast models
                tech = parts[4]
                if tech in ["OFCL", "JTWC"]:
                    valid_lines.append(parts)

            if not valid_lines:
                return []

            # Find the most recent forecast run
            latest_run = max(valid_lines, key=lambda x: x[2])[2]

            pts = []
            seen_taus = set()

            for parts in valid_lines:
                if parts[2] != latest_run:
                    continue

                tau = int(parts[5])
                # Skip TAU 0, as it overlaps with our CURRENT point from the B-Deck
                if tau == 0 or tau in seen_taus:
                    continue

                seen_taus.add(tau)
                lat, lon = self._parse_latlon(parts[6], parts[7])

                pts.append(
                    {
                        "SID": sid,
                        "NAME": sid,  # Name handled by SID grouping later
                        "LAT": lat,
                        "LON": lon,
                        "TIME": None,
                        "TYPE": "FORECAST",
                        "TAU": tau,
                    }
                )

            return pts
        except Exception as e:
            logger.debug(f"Failed to parse A-deck {url}: {e}")
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
        marker_path = self.settings.get(
            "marker_image",
            fallback=os.path.join(self.workdir, "images", "storm_symbol.png"),
        )

        storm_icon = None
        if os.path.exists(marker_path):
            try:
                storm_icon = mpimg.imread(marker_path)
            except Exception as e:
                logger.warning(f"Could not load custom storm icon: {e}")

        plot = Plot(self.map_data.region)
        plot.get_figure()

        for sid, storm_df in df.groupby("SID"):
            storm_df = storm_df.sort_values(
                ["TYPE", "TAU" if "TAU" in storm_df.columns else "LAT"]
            )

            # Grab the best name available from the PAST/CURRENT points
            named_rows = storm_df[storm_df["NAME"] != sid]
            storm_name = named_rows["NAME"].iloc[0] if not named_rows.empty else sid

            past_track = storm_df[storm_df["TYPE"] != "FORECAST"]
            future_track = storm_df[storm_df["TYPE"] != "PAST"]

            if len(future_track) >= 2:
                cone_vertices = self._build_cone_polygons(future_track)
                polygon_patch = patches.Polygon(
                    cone_vertices,
                    closed=True,
                    transform=ccrs.PlateCarree(),
                    facecolor=cone_color,
                    edgecolor=cone_color,
                    linewidth=1.0,
                    linestyle="--",
                    alpha=alpha_cone,
                    zorder=3,
                )
                plot.ax.add_patch(polygon_patch)

            plot.ax.plot(
                past_track["LON"].values,
                past_track["LAT"].values,
                transform=ccrs.PlateCarree(),
                color=track_color,
                linewidth=2.0,
                linestyle="-",
                zorder=4,
            )
            if len(future_track) > 1:
                plot.ax.plot(
                    future_track["LON"].values,
                    future_track["LAT"].values,
                    transform=ccrs.PlateCarree(),
                    color=track_color,
                    linewidth=1.5,
                    linestyle=":",
                    zorder=4,
                )

            current_pt = storm_df[storm_df["TYPE"] == "CURRENT"]
            if not current_pt.empty:
                curr_row = current_pt.iloc[0]
                lon_pt, lat_pt = curr_row["LON"], curr_row["LAT"]

                if storm_icon is not None:
                    mapped_coords = plot.ax.projection.transform_point(
                        lon_pt, lat_pt, src_crs=ccrs.PlateCarree()
                    )
                    imagebox = OffsetImage(storm_icon, zoom=marker_zoom)
                    ab = AnnotationBbox(
                        imagebox,
                        (mapped_coords[0], mapped_coords[1]),
                        frameon=False,
                        zorder=6,
                    )
                    plot.ax.add_artist(ab)
                else:
                    plot.ax.plot(
                        lon_pt,
                        lat_pt,
                        transform=ccrs.PlateCarree(),
                        marker="o",
                        color=track_color,
                        markersize=7,
                        zorder=5,
                    )

                plot.ax.text(
                    lon_pt + 0.5,
                    lat_pt + 0.5,
                    f"{storm_name}",
                    transform=ccrs.PlateCarree(),
                    color="white",
                    fontsize=10,
                    weight="bold",
                    bbox=dict(
                        facecolor="#111111",
                        alpha=0.6,
                        boxstyle="round,pad=0.2",
                        edgecolor="none",
                    ),
                    zorder=7,
                )

        plot.save_figure(self.output_path)

        logger.info("Successfully rendered active storms layer.")

    def run(self):
        self.exit_if_disabled()

        jtwc = self.settings.get("jtwc_url").strip()
        nhc_fst = self.settings.get("nhc_url").strip()
        # Derive the NHC best-track directory dynamically
        nhc_btk = nhc_fst.replace("fst", "btk")

        # 1. Collect all B-Deck files from both agencies
        b_decks = []
        for url in [jtwc, nhc_btk]:
            files = self._get_file_list(url)
            for f in files:
                if f.lower().startswith("b") and f.lower().endswith(".dat"):
                    b_decks.append(url.rstrip("/") + "/" + f)

        now_utc = datetime.now(timezone.utc)
        expiry_days = self.settings.getint("expiry_days", fallback=4)

        processed_data = []
        active_sids = []

        # 2. Parse B-Decks to find ACTIVE storms
        for b_url in b_decks:
            track_pts = self._parse_b_deck(b_url, now_utc, expiry_days)
            if track_pts:
                processed_data.extend(track_pts)
                sid = track_pts[0]["SID"]
                active_sids.append((sid, b_url))

        if not active_sids:
            logger.info("No ACTIVE storms found within expiry window. Clearing layer.")
            if os.path.exists(self.output_path):
                open(self.output_path, "w").close()  # Create empty file to wipe layer
            return

        # 3. For every active storm, hunt down its corresponding A-Deck forecast
        for sid, b_url in active_sids:
            filename = b_url.split("/")[-1]
            core_id = filename[1:].replace(".dat", "")

            # Potential matching forecast file URLs
            a_deck_urls = [
                jtwc.rstrip("/") + "/a" + core_id + ".dat",
                nhc_fst.rstrip("/") + "/" + core_id + ".fst",
            ]

            for a_url in a_deck_urls:
                fcst_pts = self._parse_a_deck(a_url, sid)
                if fcst_pts:
                    logger.info(f"Successfully matched official forecast for {sid}")
                    processed_data.extend(fcst_pts)
                    break

        df = pd.DataFrame(processed_data)
        self.plot_storms(df)
