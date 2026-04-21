#!/bin/bash
set -e

# Set up vars
source ./common.sh

# Update cloud map
# The 'create_map' executable in .venv/bin comes from the package CreateCloudMap
${PYTHON3} wallpaper_update_daemon.py --directory=${DATA} --suffix=${WORLDMAP}
