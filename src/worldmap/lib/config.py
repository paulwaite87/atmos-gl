import os
import sys
import configparser
import json
import logging

logger = logging.getLogger(__name__)

class WorldMapConfig:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        # Track the modification time to detect external changes
        self._last_mtime = self._get_current_mtime()
        self.load()

    def _get_current_mtime(self):
        """Returns the current modification time of the config file."""
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0

    def is_changed(self):
        """
        Returns True if the config file has been modified since the last check.
        Updates the internal timestamp reference.
        """
        current_mtime = self._get_current_mtime()
        if current_mtime > self._last_mtime:
            self._last_mtime = current_mtime
            return True
        return False

    def load(self):
        """Reads or re-reads the config file from disk."""
        if not os.path.exists(self.config_path):
            logger.error(f"Config file not found: {self.config_path}")
            return
        self.config.clear()
        self.config.read(self.config_path)
        self._inject_secrets()
        logger.debug(f"Configuration loaded/refreshed from {self.config_path}")

    def _inject_secrets(self):
        """Silently injects API keys from environment into the config object."""
        api_key = os.getenv("AIS_API_KEY")

        # Sections that require the AIS key
        for section in ["shipping_harvester"]:
            if self.config.has_section(section):
                if api_key:
                    # Injecting into the parser proxy
                    self.config[section]["api_key"] = api_key
                else:
                    # Fail-fast if the section is enabled but the key is missing
                    if self.config.getboolean(section, "enabled", fallback=False):
                        logger.critical(
                            f"FATAL: {section} is enabled but AIS_API_KEY is missing from Env."
                        )
                        sys.exit(1)

    def get_section(self, section):
        if self.config.has_section(section):
            return self.config[section]
        return self.config['DEFAULT']

    def section_enabled(self, section):
        if self.config.has_section(section):
            return self.config.getboolean(section, "enabled", fallback=False)
        return False

    def get_section_outfile(self, section):
        if self.config.has_section(section):
            return self.config.get(section, "outfile", fallback=None)
        return None


