# Makefile for WorldMap Project Suite

.PHONY: run stop build rebuild start-desktop stop-desktop psql logs clean purge backup restore force-map-refresh

# Variables
DB_USER = wmap
DB_NAME = worldmap
DB_SERVICE = worldmap_db
DUMP_FILE = worldmap_dump.sql

# Start/Stop/Build
run:
	docker compose up -d

start: run
	@echo "Starting"

stop:
	docker compose down

build: stop
	docker compose build

rebuild: stop
	docker compose build --no-cache

logs:
	docker compose logs -f

# Database Backups
# -Fc: Custom compressed format
backup:
	@echo "Ensuring worldmap database is running"
	docker compose up worldmap_db -d
	@echo "Creating compressed database backup to $(DUMP_FILE)..."
	docker compose exec $(DB_SERVICE) pg_dump -U $(DB_USER) -Fc $(DB_NAME) > $(DUMP_FILE)
	@echo "Backup complete."

# Database Restore
# 1. Stop dependent services to prevent active connections
# 2. Drop and Recreate database using pg_restore
# 3. Restart services
restore:
	@echo "WARNING: This will DELETE and RECREATE the $(DB_NAME) database from $(DUMP_FILE)."
	@if [ ! -f $(DUMP_FILE) ]; then echo "Error: $(DUMP_FILE) not found."; exit 1; fi
	@read -p "Are you sure? [y/N] " ans && [ $${ans:-N} = y ]
	@echo "Stopping services"
	docker compose stop shipping_collector weather_scanner map_builder
	@echo "Ensuring worldmap database is running"
	@docker compose up worldmap_db -d
	@echo "Restoring database..."
	cat $(DUMP_FILE) | docker compose exec -T $(DB_SERVICE) pg_restore -U $(DB_USER) -d postgres --clean --create --if-exists
	@echo "Restore complete."
	@echo "Stopping worldmap database."
	@docker compose stop worldmap_db

# Clean/Purge
clean:
	docker compose down --rmi all
	@echo "Containers stopped and project-specific images removed."

purge:
	@echo "WARNING: This will delete ALL containers, images, and PERSISTENT DATA volumes."
	@read -p "Are you absolutely sure? [y/N] " ans && [ $${ans:-N} = y ]
	docker compose down --rmi all --volumes
	@echo "System purged. Database volumes have been deleted."

# Desktop Management
start-desktop:
	nohup ./wallpaper_updater.sh &
	@echo "Wallpaper updater started in background."

stop-desktop:
	@pkill -f wallpaper_updater.sh || echo "No update process found."

# Database Access
psql:
	@echo "Ensuring worldmap database is running"
	@docker compose up worldmap_db -d
	@docker compose exec $(DB_SERVICE) psql -U $(DB_USER) $(DB_NAME)

# Database Status Report
# 1. Reports ship counts per region (spatial check)
# 2. Reports database composition based on lib/shipping.py logic
status:
	@echo "--- Ships Located in Each Region ---"
	@docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) $(DB_NAME) -c \
	"SELECT r.label as region, count(s.mmsi) as ships \
	 FROM map_region r \
	 LEFT JOIN ships s ON ST_Within(s.geom, r.boundary) \
	 GROUP BY r.label \
	 ORDER BY ships DESC;"
	@echo "\n--- Database Composition (Unique Ships) ---"
	@docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) $(DB_NAME) -c \
	"SELECT \
	    count(*) FILTER (WHERE name != 'Unknown' AND vessel_type != 0) as full_records, \
	    count(*) FILTER (WHERE name = 'Unknown' AND vessel_type = 0) as shadow_records, \
	    count(*) as total \
	 FROM ships;"
	@echo "--- Lightning Strikes in Each Region ---"
	@docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) $(DB_NAME) -c \
	"SELECT r.label as region, count(l.id) as strikes \
	 FROM map_region r \
	 LEFT JOIN lightning_strikes l ON ST_Within(l.geom, r.boundary) \
	 GROUP BY r.label \
	 ORDER BY strikes DESC;"

# Force the map builder to reset its schedule and run all tasks immediately
force-map-refresh:
	@docker kill --signal=SIGUSR1 map_builder
	@echo "Refresh signal sent"
