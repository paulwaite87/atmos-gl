#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import logging
import argparse
import shutil
import glob
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class MapRefreshHandler(FileSystemEventHandler):
    def __init__(self, watch_dir, pattern):
        self.watch_dir = os.path.abspath(watch_dir)
        self.pattern = pattern
        self.de = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
        self.last_applied = None
        logger.info(f"Monitoring {self.pattern} in {self.watch_dir} (DE: {self.de})")

    def get_latest_file(self):
        """Finds the newest file matching the pattern *-suffix."""
        search_path = os.path.join(self.watch_dir, f"*-{self.pattern}")
        files = glob.glob(search_path)
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def on_any_event(self, event):
        if event.is_directory:
            return

        # Trigger on file lifecycle events
        if event.event_type in ('created', 'modified', 'moved'):
            latest = self.get_latest_file()

            # Prevent feedback loop from cleanup logic
            if latest and latest != self.last_applied:
                # Brief sleep to ensure the file write/move is finalized
                time.sleep(0.5)
                self.apply_wallpaper(latest)

    def apply_wallpaper(self, path):
        uri = f"file://{path}"
        try:
            # --- KDE PLASMA (Surgical D-Bus method) ---
            if 'plasma' in self.de or 'kde' in self.de:
                # We use JS to update the wallpaper property only,
                # avoiding a full desktop layout reset.
                script = f"""
                var allDesktops = desktops();
                for (i=0; i<allDesktops.length; i++) {{
                    d = allDesktops[i];
                    d.wallpaperPlugin = "org.kde.image";
                    d.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
                    d.writeConfig("Image", "{uri}");
                }}
                """
                subprocess.run([
                    "qdbus", "org.kde.plasmashell", "/PlasmaShell",
                    "org.kde.PlasmaShell.evaluateScript", script
                ], check=True)

            # --- GNOME / UNITY / POP!_OS ---
            elif any(name in self.de for name in ['gnome', 'unity', 'ubuntu', 'pop']):
                subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri], check=True)
                subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri],
                               check=True)
                subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-options", "zoom"],
                               check=True)

            # --- XFCE ---
            elif 'xfce' in self.de:
                cmd = f"xfconf-query -c xfce4-desktop -p /backdrop -l | grep last-image | xargs -I % xfconf-query -c xfce4-desktop -p % -s '{path}'"
                subprocess.run(cmd, shell=True, check=True)

            # --- CINNAMON ---
            elif 'cinnamon' in self.de:
                subprocess.run(["gsettings", "set", "org.cinnamon.desktop.background", "picture-uri", uri], check=True)

            # --- MATE ---
            elif 'mate' in self.de:
                subprocess.run(["gsettings", "set", "org.mate.background", "picture-filename", path], check=True)

            # --- FALLBACK (FEH) ---
            else:
                if shutil.which("feh"):
                    subprocess.run(["feh", "--bg-fill", path], check=True)
                else:
                    logger.error("No compatible wallpaper manager found.")

            logger.info(f"Wallpaper updated: {os.path.basename(path)}")
            self.last_applied = path

            # Clean up old files to prevent disk usage growth
            self.cleanup_old_files(path)

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to update wallpaper: {e}")

    def cleanup_old_files(self, current_path):
        """Delete all files matching pattern except the one currently in use."""
        search_path = os.path.join(self.watch_dir, f"*-{self.pattern}")
        all_maps = glob.glob(search_path)
        for m in all_maps:
            if os.path.abspath(m) != os.path.abspath(current_path):
                try:
                    os.remove(m)
                except OSError:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="World Map Wallpaper Refresh Daemon")
    parser.add_argument("--directory", type=str, required=True, help="Directory to watch for new renders")
    parser.add_argument("--suffix", type=str, default="worldmap.jpg",
                        help="Suffix of the render files (e.g. worldmap.jpg)")
    args = parser.parse_args()

    watch_path = Path(args.directory).absolute()
    if not watch_path.exists():
        logger.error(f"Path not found: {watch_path}")
        sys.exit(1)

    handler = MapRefreshHandler(str(watch_path), args.suffix)

    # Perform initial refresh on startup
    initial_map = handler.get_latest_file()
    if initial_map:
        logger.info(f"Startup refresh: {initial_map}")
        handler.apply_wallpaper(initial_map)

    # Initialize Watchdog
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=False)

    logger.info(f"Daemon active. Watching {watch_path}...")
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        observer.stop()
    observer.join()