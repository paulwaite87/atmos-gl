#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Styling variables
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

MAP_SERVICE=map_builder
DB_SERVICE=worldmap_db
DB_USER=wmap
DB_NAME=worldmap

echo -e "${BLUE}=== WorldMap ===${NC}"

# Check for prerequisites
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed."
    exit 1
fi

if [ ! -f ./docker-compose.yml ]; then
    echo "Error: not in worldmap root; no docker-compose.yml found"
    exit 1
fi

case "$1" in
    start)
      docker compose up -d
      ;;
    stop)
      docker compose down
      ;;
    restart)
      docker compose restart
      ;;
    logs)
      docker compose logs -f
      ;;
    status)
      docker compose ps
      ;;
    map-start)
      nohup ./wallpaper_update.sh > wallpaper.log 2>&1 & echo "Daemon started (logs: wallpaper.log)"
      ;;
    map-stop)
      pkill -f wallpaper_update_daemon.py && echo "Daemon stopped"
      ;;
    db)
      docker compose exec ${DB_SERVICE} psql -U ${DB_USER} ${DB_NAME}
      ;;
    status)
      echo "--- Ships Located in Each Region ---"
      docker compose exec -T ${DB_SERVICE} psql -U ${DB_USER} ${DB_NAME} -c \
      "SELECT r.label as region, count(s.mmsi) as ships \
       FROM map_region r \
       LEFT JOIN ships s ON ST_Within(s.geom, r.boundary) \
       GROUP BY r.label \
       ORDER BY ships DESC;"
      echo "\n--- Database Composition (Unique Ships) ---"
      docker compose exec -T ${DB_SERVICE} psql -U ${DB_USER} ${DB_NAME} -c \
      "SELECT \
          count(*) FILTER (WHERE name != 'Unknown' AND vessel_type != 0) as full_records, \
          count(*) FILTER (WHERE name = 'Unknown' AND vessel_type = 0) as shadow_records, \
          count(*) as total \
       FROM ships;"
      echo "--- Lightning Strikes in Each Region ---"
      docker compose exec -T ${DB_SERVICE} psql -U ${DB_USER} ${DB_NAME} -c \
      "SELECT r.label as region, count(l.id) as strikes \
       FROM map_region r \
       LEFT JOIN lightning_strikes l ON ST_Within(l.geom, r.boundary) \
       GROUP BY r.label \
       ORDER BY strikes DESC;"
      ;;
    refresh-map)
      docker kill --signal=SIGUSR1 ${MAP_SERVICE}
      echo "Refresh signal sent"
      ;;

    *)
      echo "Usage: worldmap {start|stop|restart|logs|status|map-start|map-stop|db}"
      ;;
esac
EOF
chmod +x worldmap.sh

# Start the system
echo -e "${BLUE}Starting World Map...${NC}"
./worldmap.sh start

echo -e "${GREEN}=== Installation Complete! ===${NC}"
echo "System initialized. Please update your settings:"
echo "API Keys: ${GREEN}$INSTALL_DIR/.env${NC}"
echo "Configuration: ${GREEN}$INSTALL_DIR/config/worldmap.conf${NC}"
echo "   Web UI: http://localhost:8180/"
echo "Use ${GREEN}$INSTALL_DIR/worldmap.sh${NC} to manage the system."
echo ""