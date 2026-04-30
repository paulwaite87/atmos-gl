import os
import sys
import logging

# Get the level from environment, default to INFO if not set
log_level_str = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Map string to logging constants
# This handles 'DEBUG', 'INFO', 'WARNING', etc.
log_level = getattr(logging, log_level_str, logging.INFO)

def setup_logging(level=log_level):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
