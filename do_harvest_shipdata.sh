#!/bin/bash
set -e

# Set up common vars
source ./common.sh

echo "Beginning ship data harvest"

# See [shipping_harvester] section in update_map.ini
${PYTHON3} ${SCRIPTS}/harvest_ship_data --config=${WORLDMAP_CONFIG_FILE}

echo "Finished"
