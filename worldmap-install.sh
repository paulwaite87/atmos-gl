#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Styling variables
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}=== WorldMap Quick Installer ===${NC}"

# 1. Determine installation directory
# Use the first argument provided, or default to $HOME/worldmap
TARGET_DIR="${1:-$HOME/worldmap}"
INSTALL_DIR=$(realpath "$TARGET_DIR")

echo -e "Setting up World Map in ${GREEN}${INSTALL_DIR}${NC}..."
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# 2. Check for prerequisites
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed. Please install Docker first."
    exit 1
fi

# 3. Handle .env file
if [ ! -f .env ]; then
    echo -e "${YELLOW}Please provide API keys (or ENTER for none is ok)${NC}"
    read -p "Enter AIS_API_KEY: " ais_key
    read -p "Enter OPENWEATHER_API_KEY: " weather_key

    echo "AIS_API_KEY=$ais_key" > .env
    echo "OPENWEATHER_API_KEY=$weather_key" >> .env
    echo -e "Configuration saved to ${GREEN}${INSTALL_DIR}/.env${NC}"
else
    echo -e "Existing ${GREEN}.env${NC} file found, skipping setup."
fi

# 4. Download the production docker-compose file
echo "Downloading configuration..."
curl -fsSL https://raw.githubusercontent.com/paulwaite87/worldmap/refs/heads/master/docker-compose-prod.yml -o docker-compose.yml

# 5. Start the system
echo -e "${BLUE}Starting World Map containers...${NC}"
docker compose -f docker-compose.yml up -d

echo -e "${GREEN}=== Installation Complete! ===${NC}"
echo "World Map is now running in the background."
echo "You can update your API keys anytime by editing: ${GREEN}${INSTALL_DIR}/.env${NC}"