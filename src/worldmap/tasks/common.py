#!/usr/bin/env python3
import os
import re
import sys
import logging
import configparser
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# The third-party library
from cloudmap.create_map import main as cloudmap_main

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)

class Updater:
    def __init__(self, config: WorldMapConfig, section: str):
        self.config = config
        self.section = section
        self.settings = config.get_section(section.lower())
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")
        self.output_path = ""

    def exit_if_disabled(self):
        if not self.settings.getboolean("enabled", fallback=False):
            logger.info(f"{self.section} task disabled; skipping.")
            sys.exit(0)

    def set_output_path(self):
        self.output_path = str(os.path.join(
            self.common.get("workdir", "."),
            self.settings.get("outfile"))
        )
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        with open(self.output_path, "w") as _:
            pass
