import os
import sys
import logging

# Get the level from environment, default to INFO if not set
log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()

# Map string to logging constants
log_level = getattr(logging, log_level_str, logging.INFO)


def setup_logging(level=log_level):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def set_loglevel(level):
    """
    Sets a new logging level for the root logger only if it differs from the current level.
    'level' can be a string ('DEBUG') or a logging constant (logging.DEBUG).
    """
    if isinstance(level, str):
        # Fallback to current level if an invalid string is passed
        level = getattr(logging, level.upper(), logging.getLogger().getEffectiveLevel())

    root_logger = logging.getLogger()
    if root_logger.getEffectiveLevel() != level:
        root_logger.setLevel(level)
