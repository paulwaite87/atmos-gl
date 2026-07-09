#!/usr/bin/env python3
"""NHC / JTWC tropical-cyclone feeds -> database.

Pure data (no render): scrapes ATCF b-deck/a-deck files, builds tracks + forecast cones,
and upserts storms. The frontend reads them via the /api/storms route.

HEAD check: both ATCF source directories (JTWC and NHC) are served via standard HTTP and
carry Last-Modified headers that update when files are added or changed. We skip the
directory scrape entirely if neither directory has changed since the last run.
"""
import logging
import math
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.storm_adapter import StormAdapter

logger = logging.getLogger(__name__)


class StormsCollector(CollectorBase):
    section = "storms"

    def __init__(self, config):
        super().__init__(config)
        self.storm_adapter = StormAdapter()

    def has_new_data(self) -> bool:
        """HEAD both ATCF directory URLs; skip if neither has changed."""
        jtwc = self.settings.get("jtwc_url", "").strip().rstrip("/")
        nhc = self.settings.get("nhc_url", "").strip().rstrip("/")
        changed = False
        for url in filter(None, [jtwc, nhc]):
            result = self._head_changed(url)
            if result is None or result:
                changed = True  # failed or changed → be conservative
        if not changed:
            logger.debug("Storms: ATCF directories unchanged; skipping collect.")
        return changed

    def _get_file_list(self, directory_url):
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
            logger.debug(f"Storms: failed to list {directory_url}: {e}")
            return []

    def _parse_latlon(self, lat_str, lon_str):
        lat_val = float(lat_str[:-1]) * 0.1
        if lat_str.endswith("S"):
            lat_val = -lat_val
        lon_val = float(lon_str[:-1]) * 0.1
        if lon_str.endswith("W"):
            lon_val = -lon_val
        return lat_val, lon_val

    def _parse_b_deck(self, url, now_utc, expiry_days):
        try:
            text = requests.get(url, timeout=10).text
            lines = text.splitlines()
            pts = []
            storm_name = None
            filename = url.split("/")[-1]
            sid = filename[1:9].upper()

            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 10:
                    continue
                if parts[4] == "BEST":
                    dt_str = parts[2]
                    dt = datetime.strptime(dt_str, "%Y%m%d%H").replace(
                        tzinfo=timezone.utc
                    )
                    lat, lon = self._parse_latlon(parts[6], parts[7])
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

            final_name = storm_name or sid
            for p in pts:
                p["NAME"] = final_name

            latest_time = pts[-1]["TIME"]
            if (now_utc - latest_time) > timedelta(days=expiry_days):
                return []

            pts[-1]["TYPE"] = "CURRENT"
            return pts
        except Exception as e:
            logger.debug(f"Storms: failed to parse B-deck {url}: {e}")
            return []

    def _parse_a_deck(self, url, sid):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                return []
            lines = r.text.splitlines()
            valid_lines = [
                [p.strip() for p in line.split(",")]
                for line in lines
                if len(line.split(",")) >= 10
                and line.split(",")[4].strip() in ["OFCL", "JTWC"]
            ]
            if not valid_lines:
                return []

            latest_run = max(valid_lines, key=lambda x: x[2])[2]
            pts = []
            seen_taus = set()
            for parts in valid_lines:
                if parts[2] != latest_run:
                    continue
                tau = int(parts[5])
                if tau == 0 or tau in seen_taus:
                    continue
                seen_taus.add(tau)
                lat, lon = self._parse_latlon(parts[6], parts[7])
                run_dt = datetime.strptime(latest_run, "%Y%m%d%H").replace(
                    tzinfo=timezone.utc
                )
                pts.append(
                    {
                        "SID": sid,
                        "LAT": lat,
                        "LON": lon,
                        "TIME": run_dt + timedelta(hours=tau),
                        "TYPE": "FORECAST",
                        "TAU": tau,
                    }
                )
            return pts
        except Exception as e:
            logger.debug(f"Storms: failed to parse A-deck {url}: {e}")
            return []

    def _build_cone_polygons(self, future_track):
        if len(future_track) < 2:
            return []

        left_points = []
        right_points = []

        for idx, row in enumerate(future_track):
            lat, lon = row["LAT"], row["LON"]
            tau = int(row.get("TAU", 0) or 0)
            r_degrees = max(0.05, tau * 0.045)
            r_lon = r_degrees / math.cos(math.radians(lat))
            r_lat = r_degrees

            if idx < len(future_track) - 1:
                next_row = future_track[idx + 1]
                dlat = next_row["LAT"] - lat
                dlon = (next_row["LON"] - lon) * math.cos(math.radians(lat))
            else:
                prev_row = future_track[idx - 1]
                dlat = lat - prev_row["LAT"]
                dlon = (lon - prev_row["LON"]) * math.cos(math.radians(lat))

            heading = math.atan2(dlat, dlon)
            left_points.append(
                (
                    lon + r_lon * math.cos(heading + math.pi / 2),
                    lat + r_lat * math.sin(heading + math.pi / 2),
                )
            )
            right_points.append(
                (
                    lon + r_lon * math.cos(heading - math.pi / 2),
                    lat + r_lat * math.sin(heading - math.pi / 2),
                )
            )

        return left_points + right_points[::-1] + [left_points[0]]

    def collect(self) -> None:
        jtwc = self.settings.get("jtwc_url", "").strip()
        nhc_fst = self.settings.get("nhc_url", "").strip()
        nhc_btk = nhc_fst.replace("fst", "btk")
        expiry_days = self.settings.get("expiry_days", 4)

        self.storm_adapter.prune_expired_storms(expiry_days)

        b_decks = []
        for url in [jtwc, nhc_btk]:
            for f in self._get_file_list(url):
                if f.lower().startswith("b") and f.lower().endswith(".dat"):
                    b_decks.append(url.rstrip("/") + "/" + f)

        now_utc = datetime.now(timezone.utc)
        active_storms = []

        for b_url in b_decks:
            filename = b_url.split("/")[-1]
            try:
                storm_num = int(filename[3:5])
                if 80 <= storm_num <= 89:
                    continue
            except ValueError:
                pass

            track_pts = self._parse_b_deck(b_url, now_utc, expiry_days)
            if track_pts:
                sid = track_pts[0]["SID"]
                active_storms.append(
                    {
                        "sid": sid,
                        "name": track_pts[-1].get("NAME", sid),
                        "b_url": b_url,
                        "track": track_pts,
                    }
                )

        if not active_storms:
            logger.info("Storms: no active storms within expiry window.")
            return

        for storm in active_storms:
            sid = storm["sid"]
            filename = storm["b_url"].split("/")[-1]
            core_id = filename[1:].replace(".dat", "")

            fcst_pts = []
            for a_url in [
                jtwc.rstrip("/") + "/a" + core_id + ".dat",
                nhc_fst.rstrip("/") + "/" + core_id + ".fst",
            ]:
                fcst_pts = self._parse_a_deck(a_url, sid)
                if fcst_pts:
                    logger.info(f"Storms: matched official forecast for {sid}.")
                    break

            full_track = storm["track"] + fcst_pts
            current_pt = [p for p in full_track if p.get("TYPE") == "CURRENT"]
            cone_input = current_pt + fcst_pts if fcst_pts else []
            cone_vertices = self._build_cone_polygons(cone_input) if cone_input else []

            self.storm_adapter.update_storm(
                sid=sid,
                name=storm["name"],
                cone_vertices=cone_vertices,
                track_points=full_track,
            )
            logger.debug(
                f"Storms: upserted {sid} ({storm['name']}) with "
                f"{len(full_track)} track points."
            )
