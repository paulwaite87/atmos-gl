import sys
import subprocess
import time
import signal
import logging
import argparse
from datetime import datetime

# Setup logging to stdout so Docker picks it up
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class Daemon:
    def __init__(self, map_updater, update_sleep, harvester, harvest_sleep, morning, evening):
        self.map_updater = map_updater
        self.update_sleep = update_sleep
        self.harvester = harvester
        self.harvest_sleep = harvest_sleep
        self.morning_time = morning
        self.evening_time = evening
        self.running = True

        # Signal handling for graceful Docker stops
        signal.signal(signal.SIGTERM, self.handle_exit)
        signal.signal(signal.SIGINT, self.handle_exit)

    def handle_exit(self, signum, frame):
        logger.info(f"Signal {signum} detected. Stopping daemon...")
        self.running = False

    def is_morning_shift(self):
        """Determines if current time falls between morning and evening start times."""
        now = datetime.now().strftime('%H:%M')
        # String comparison works for HH:MM format
        return self.morning_time <= now < self.evening_time

    def run(self):
        logger.info("Map Update/Harvester Daemon Started.")
        logger.info(f"Morning Shift ({self.morning_time}): {self.map_updater} (Sleep {self.update_sleep}s)")
        logger.info(f"Evening Shift ({self.evening_time}): {self.harvester} (Sleep {self.harvest_sleep}s)")

        while self.running:
            # 1. Determine current mode
            if self.is_morning_shift():
                current_task = self.map_updater
                current_sleep = self.update_sleep
                mode_label = "MAP UPDATE"
            else:
                current_task = self.harvester
                current_sleep = self.harvest_sleep
                mode_label = "HARVESTER"

            # 2. Execute the task
            try:
                logger.info(f"[{mode_label}] Executing task...")
                result = subprocess.run(current_task, shell=True, check=True)
                logger.info(f"Task completed successfully (exit code {result.returncode}).")
            except subprocess.CalledProcessError as e:
                logger.error(f"Task failed with exit code: {e.returncode}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")

            # 3. Simple sleep for the defined interval
            if self.running:
                logger.info(f"Waiting {current_sleep}s before next run...")
                stop_at = time.time() + current_sleep
                # Loop in small steps so we can catch SIGTERM/SIGINT immediately
                while time.time() < stop_at and self.running:
                    time.sleep(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Python Daemon Wrapper")
    parser.add_argument("--map-updater", required=True, help="Command to refresh World map")
    parser.add_argument("--update-sleep", type=int, default=300, help="Sleep time after map update (sec)")
    parser.add_argument("--harvester", required=True, help="Command to harvest ship data")
    parser.add_argument("--harvest-sleep", type=int, default=1800, help="Sleep time after harvesting (sec)")
    parser.add_argument("--morning", default='09:00', help="Time to switch to map updates (HH:MM)")
    parser.add_argument("--evening", default='19:00', help="Time to switch to harvesting (HH:MM)")

    args = parser.parse_args()

    daemon = Daemon(
        map_updater=args.map_updater,
        update_sleep=args.update_sleep,
        harvester=args.harvester,
        harvest_sleep=args.harvest_sleep,
        morning=args.morning,
        evening=args.evening
    )
    daemon.run()