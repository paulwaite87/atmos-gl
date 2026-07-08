import os
import json
import logging

from worldmap.lib.logging import set_loglevel

logger = logging.getLogger(__name__)
set_loglevel("INFO")


class WorldMapConfig:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = {}
        # Track the modification time to detect external changes
        self._last_mtime = self._get_current_mtime()
        self.has_changed = False
        self.load()

    def _get_current_mtime(self):
        """Returns the current modification time of the config file."""
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0

    def check_if_changed(self) -> bool:
        """
        Returns True if the config file has been modified since the last check.
        Updates the internal timestamp reference, and stores the result.
        """
        current_mtime = self._get_current_mtime()
        if current_mtime > self._last_mtime:
            self._last_mtime = current_mtime
            return True
        return False

    def load(self):
        """Reads or re-reads the JSON config file from disk."""
        if not os.path.exists(self.config_path):
            logger.error(f"Config file not found: {self.config_path}")
            return

        try:
            with open(self.config_path, "r") as config_file:
                self.config = json.load(config_file)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON config file {self.config_path}: {e}")
            return

        # Read secrets and insert them into the config dict
        self._inject_secrets()

        # Monitor change status
        self.has_changed = self.check_if_changed()

        # Adjust log level for common (overall) logging
        log_level = self.get_setting("common", "log_level")
        if log_level:
            set_loglevel(log_level)

    def save(self):
        """Saves the config dictionary back to disk as formatted JSON."""
        self._delete_secrets()
        with open(self.config_path, "w") as config_file:
            json.dump(self.config, config_file, indent=2)

    def _inject_secrets(self):
        """Silently injects API keys from environment into the config object."""
        # Sections requiring an API key
        api_keys = {
            "shipping_collector": os.getenv("AIS_API_KEY"),
            "lightning_collector": os.getenv("OPENWEATHER_API_KEY"),
            "common": os.getenv("MAPTILER_API_KEY"),
        }

        for section, api_key in api_keys.items():
            if section in self.config and api_key:
                self.config[section]["api_key"] = api_key

    def _delete_secrets(self):
        """Removes sensitive keys from the config dict before saving."""
        for section, settings in self.config.items():
            if isinstance(settings, dict) and "api_key" in settings:
                del settings["api_key"]

    def get_section(self, section):
        return self.config.get(section, {})

    def section_enabled(self, section):
        """Returns the native boolean value for 'enabled'."""
        return self.config.get(section, {}).get("enabled", False)

    def get_section_outfile(self, section):
        return self.config.get(section, {}).get("outfile", None)

    def get_setting(self, section, setting, default=None):
        return self.config.get(section, {}).get(setting, default)

    def update_setting(self, section, setting, value):
        if section not in self.config:
            self.config[section] = {}
        self.config[section][setting] = value
