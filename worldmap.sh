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
    remove)
      echo -e "${YELLOW}Stopping services, removing containers, and deleting images...${NC}"
      docker compose down --rmi all
      echo -e "${GREEN}Containers and images removed. Data and configuration files preserved.${NC}"
      ;;
    purge)
      echo -e "${YELLOW}WARNING: This will stop all services and delete ALL data (including database and logs).${NC}"
      read -p "Are you sure you want to purge World Map? [y/N] " confirm
      if [[ $confirm == [yY] ]]; then
        docker compose down -v
        rm -rf ./data ./config .env
        echo -e "${GREEN}Purge complete. All containers, volumes, and local data have been removed.${NC}"
      else
        echo "Purge cancelled."
      fi
      ;;
    *)
      echo "WorldMap Management Script"
      echo "Usage: ./worldmap.sh {command}"
      echo ""
      echo "Commands:"
      echo "  start           Start all containers in the background"
      echo "  stop            Stop all containers"
      echo "  restart         Restart all containers"
      echo "  logs            Follow logs from all containers"
      echo "  status          Show some database statistics statistics"
      echo "  db              Open a PostgreSQL shell for the database"
      echo "  refresh-map     Force a map refresh via signal"
      echo "  map-start       Start the wallpaper daemon"
      echo "  map-stop        Stop the wallpaper daemon"
      echo "  remove          Stop services, remove containers and images (keep data)"
      echo "  purge           Full reset: remove containers, images, volumes, and data"
      ;;
esac
