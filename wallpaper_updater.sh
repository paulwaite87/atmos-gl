#!/bin/bash
set -e

# Define paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv_wallpaper"
IMAGE_DIR="$SCRIPT_DIR/data"

# Setup/Update Virtual Environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating host-side VENV for wallpaper daemon..."
    python3 -m venv "$VENV_DIR"
fi

# Ensure watchdog is installed
"$VENV_DIR/bin/pip" install -q watchdog

# Handle arguments
update_opt=""
if [[ "$1" == "-once" || "$1" == "--once" ]]; then
    update_opt="--once"
fi

# Run the daemon using the VENV python
echo "Starting wallpaper daemon..."
"$VENV_DIR/bin/python" "$SCRIPT_DIR/wallpaper_update_daemon.py" ${update_opt} \
  --directory="$IMAGE_DIR" \
  --suffix="regionmap.jpg"
