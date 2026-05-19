# Makefile for WorldMap Project Suite

.PHONY: run stop build rebuild start-desktop stop-desktop psql logs clean purge backup restore force-map-refresh test

# Variables
DB_USER = wmap
DB_NAME = worldmap
DB_SERVICE = worldmap_db
DUMP_FILE = worldmap_dump.sql

## run: Bring all containers up in background, build if required
run:
	docker compose up -d

## stop: Bring all containers down
stop:
	docker compose down

## build: Build images if changed
build: stop
	docker compose build

## rebuild: Rebuild all images from scratch
rebuild: stop
	docker compose build --no-cache

## logs: Tail logs for all containers
logs:
	docker compose logs -f

## test: Run all tests
test:
	@echo "Ensuring map_builder container environment is active..."
	docker compose up map_builder -d
	@echo "Running complete discovery test suite via pytest..."
	docker compose exec map_builder pytest tests/ -v

## bash: Start a bash session in the main map_builder app
bash:
	@echo "Ensuring map_builder container environment is active..."
	docker compose up map_builder -d
	docker compose exec map_builder bash

## backup: Backup database to local worldmap-dump.sql file
backup:
	@echo "Ensuring worldmap database is running"
	docker compose up worldmap_db -d
	@echo "Creating compressed database backup to $(DUMP_FILE)..."
	docker compose exec $(DB_SERVICE) pg_dump -U $(DB_USER) -Fc $(DB_NAME) > $(DUMP_FILE)
	@echo "Backup complete."

## restore: Restores the local worldmap-dump.sql backup
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

## clean: Stop containers, remove images
clean:
	docker compose down --rmi all
	@echo "Containers stopped and project-specific images removed."

## purge: DANGEROUS! Delete all containers, images and data volumes
purge:
	@echo "WARNING: This will delete ALL containers, images, and PERSISTENT DATA volumes."
	@read -p "Are you absolutely sure? [y/N] " ans && [ $${ans:-N} = y ]
	docker compose down --rmi all --volumes
	@echo "System purged. Database volumes have been deleted."

## stop-desktop: Kill the desktop background updater
stop-desktop:
	@pkill -f wallpaper_updater.sh || echo "No update process found."

## start-desktop: Start the desktop map updater in the foreground
start-desktop-fg:
	./wallpaper_updater.sh
	@echo "Wallpaper updater started."

## psql: Open psql session in map database
psql:
	@echo "Ensuring worldmap database is running"
	@docker compose up worldmap_db -d
	@docker compose exec $(DB_SERVICE) psql -U $(DB_USER) $(DB_NAME)

## status: Database Status Report
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

## refresh-map: Force map builder to refresh wallpaper
refresh-map:
	@docker kill --signal=SIGUSR1 map_builder
	@echo "Refresh signal sent"

## help: Show this help menu
help:
	@echo "Available commands:"
	@sed -n 's/^##//p' $(MAKEFILE_LIST) | awk '{printf "  \033[36m%-20s\033[0m %s\n", $$1, substr($$0, index($$0,$$2))}'
