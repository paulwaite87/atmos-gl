#!/usr/bin/env python3
import os
import logging
import requests
import math
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# Internal library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class StormUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "storms", map_data)

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

                # Forecast times are derived by adding TAU hours to the run time
                run_dt = datetime.strptime(latest_run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
                fcst_dt = run_dt + timedelta(hours=tau)

                pts.append(
                    {
                        "SID": sid,
                        "LAT": lat,
                        "LON": lon,
                        "TIME": fcst_dt,
                        "TYPE": "FORECAST",
                        "TAU": tau,
                    }
                )

            return pts
        except Exception as e:
            logger.debug(f"Failed to parse A-deck {url}: {e}")
            return []

    def _build_cone_polygons(self, future_track):
        """
        Calculates geographic error envelopes using a latitude-compensated
        radius to prevent horizontal distortion.
        """
        if len(future_track) < 2:
            return []

        # Convert to radians for math
        def to_rad(deg):
            return math.radians(deg)

        def to_deg(rad):
            return math.degrees(rad)

        left_points = []
        right_points = []

        for idx, row in enumerate(future_track):
            lat, lon = row["LAT"], row["LON"]
            # Ensure TAU exists, default to 0 if missing
            tau = int(row.get("TAU", 0) or 0)

            # Make the radius scale strictly with time.
            # We enforce a tiny minimum (0.05) so PostGIS doesn't complain about
            # invalid self-intersecting polygons at the exact tip.
            r_degrees = max(0.05, tau * 0.045)

            # Apply cosine correction to longitude:
            # As we move away from the equator, the 'distance' of a degree of longitude
            # changes, so we scale the radius proportionally.
            r_lon = r_degrees / math.cos(to_rad(lat))
            r_lat = r_degrees

            # Determine heading using previous/next point
            if idx < len(future_track) - 1:
                next_row = future_track[idx + 1]
                dlat = next_row["LAT"] - lat
                dlon = (next_row["LON"] - lon) * math.cos(to_rad(lat))
            else:
                prev_row = future_track[idx - 1]
                dlat = lat - prev_row["LAT"]
                dlon = (lon - prev_row["LON"]) * math.cos(to_rad(lat))

            heading = math.atan2(dlat, dlon)

            # Calculate perpendicular offsets (90 degrees = pi/2)
            # We use the corrected r_lon/r_lat here
            left_lat = lat + r_lat * math.sin(heading + math.pi / 2)
            left_lon = lon + r_lon * math.cos(heading + math.pi / 2)

            right_lat = lat + r_lat * math.sin(heading - math.pi / 2)
            right_lon = lon + r_lon * math.cos(heading - math.pi / 2)

            left_points.append((left_lon, left_lat))
            right_points.append((right_lon, right_lat))

        # Create a closed polygon loop (start -> end -> reverse back to start)
        # Ensure the polygon is "closed" by appending the first point to the end
        full_ring = left_points + right_points[::-1] + [left_points[0]]

        return full_ring

    def run(self):
        self.exit_if_disabled()
        db = Database()

        jtwc = self.settings.get("jtwc_url").strip()
        nhc_fst = self.settings.get("nhc_url").strip()
        nhc_btk = nhc_fst.replace("fst", "btk")

        expiry_days = self.settings.get("expiry_days", 4)

        # 0. Clean up old storms from the database
        db.prune_expired_storms(expiry_days)

        # 1. Collect all B-Deck files
        b_decks = []
        for url in [jtwc, nhc_btk]:
            files = self._get_file_list(url)
            for f in files:
                if f.lower().startswith("b") and f.lower().endswith(".dat"):
                    b_decks.append(url.rstrip("/") + "/" + f)

        now_utc = datetime.now(timezone.utc)
        active_storms = []

        # 2. Parse B-Decks to find ACTIVE storms
        for b_url in b_decks:
            filename = b_url.split("/")[-1]

            try:
                # Filter out NOAA internal training storms (80-89)
                storm_num = int(filename[3:5])
                if 80 <= storm_num <= 89:
                    continue
            except ValueError:
                pass

            track_pts = self._parse_b_deck(b_url, now_utc, expiry_days)
            if track_pts:
                sid = track_pts[0]["SID"]
                storm_name = track_pts[-1].get("NAME", sid)

                # We store the active storm metadata to process A-Decks next
                active_storms.append({
                    "sid": sid,
                    "name": storm_name,
                    "b_url": b_url,
                    "track": track_pts
                })

        if not active_storms:
            logger.info("No ACTIVE storms found within expiry window.")
            return

        # 3. Process Forecasts and Push to Database
        for storm in active_storms:
            sid = storm["sid"]
            filename = storm["b_url"].split("/")[-1]
            core_id = filename[1:].replace(".dat", "")

            # Potential matching forecast file URLs
            a_deck_urls = [
                jtwc.rstrip("/") + "/a" + core_id + ".dat",
                nhc_fst.rstrip("/") + "/" + core_id + ".fst",
            ]

            fcst_pts = []
            for a_url in a_deck_urls:
                fcst_pts = self._parse_a_deck(a_url, sid)
                if fcst_pts:
                    logger.info(f"Matched official forecast for {sid}")
                    break

            # Combine historical and forecast tracks
            full_track = storm["track"] + fcst_pts

            # Extract the single CURRENT point from the track
            current_pt = [p for p in full_track if p.get('TYPE') == 'CURRENT']

            # Combine CURRENT point + FORECAST points for the cone generator
            cone_input = current_pt + fcst_pts if fcst_pts else []
            cone_vertices = self._build_cone_polygons(cone_input) if cone_input else []

            # Save directly into database
            db.update_storm(
                sid=sid,
                name=storm["name"],
                cone_vertices=cone_vertices,
                track_points=full_track
            )
            logger.debug(f"Upserted storm {sid} ({storm['name']}) into database with {len(full_track)} track points.")
