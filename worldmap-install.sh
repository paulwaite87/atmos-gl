#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Styling variables
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'
WORLDMAP_RAW_URL=https://raw.githubusercontent.com/paulwaite87/worldmap/refs/heads/master

echo -e "${BLUE}=== WorldMap Quick Installer ===${NC}"

# Determine installation directory
TARGET_DIR="${1:-$HOME/worldmap}"
INSTALL_DIR=$(realpath "$TARGET_DIR")
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Check for prerequisites
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed."
    exit 1
fi

# Download and setup configuration
echo "Downloading configuration templates..."
mkdir -p config

# Fetch templates from repository
curl -fsSL ${WORLDMAP_RAW_URL}/docker-compose-prod.yml -o docker-compose.yml
curl -fsSL ${WORLDMAP_RAW_URL}/config/worldmap.json.example -o config/worldmap.json

# Fetch and setup .env
if [ ! -f .env ]; then
    curl -fsSL ${WORLDMAP_RAW_URL}/.env.tmpl -o .env
    echo -e "${YELLOW}Template .env created. Please edit it to add your API keys.${NC}"
fi

# Download the wallpaper daemon scripts
echo "Setting up wallpaper daemon..."
curl -fsSL ${WORLDMAP_RAW_URL}/wallpaper_update_daemon.py -o wallpaper_update_daemon.py
curl -fsSL ${WORLDMAP_RAW_URL}/wallpaper_updater.sh -o wallpaper_updater.sh
chmod +x wallpaper_updater.sh

# Download the 'worldmap.sh' control script
curl -fsSL ${WORLDMAP_RAW_URL}/worldmap.sh -o worldmap.sh
chmod +x worldmap.sh

# Start the system
echo -e "${BLUE}Starting World Map...${NC}"
./worldmap.sh start

echo -e "${GREEN}=== Installation Complete! ===${NC}"
echo "System initialized. Please update your settings:"
echo "API Keys: ${GREEN}${INSTALL_DIR}/.env${NC}"
echo "Configuration: ${GREEN}${INSTALL_DIR}/config/worldmap.json${NC}"
echo "   Web UI: http://localhost:8180/"
echo "Use ${GREEN}${INSTALL_DIR}/worldmap.sh${NC} to manage the system."
echo ""