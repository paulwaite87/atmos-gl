import os
import sys
import configparser
import logging

logger = logging.getLogger(__name__)


class WorldMapConfig:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load()

    def load(self):
        """Reads or re-reads the config file from disk."""
        if not os.path.exists(self.config_path):
            logger.error(f"Config file not found: {self.config_path}")
            return

        self.config.read(self.config_path)
        self._inject_secrets()
        logger.info(f"Configuration loaded/refreshed from {self.config_path}")

    def _inject_secrets(self):
        """Silently injects API keys from environment into the config object."""
        api_key = os.getenv("AIS_API_KEY")

        # Sections that require the AIS key
        for section in ["shipping", "shipping_harvester"]:
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
        if section in self.config:
            return self.config[section]
        return {}
