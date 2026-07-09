#!/usr/bin/env bash
# WorldMap control script -- start/stop/monitor the pre-built Docker Compose stack.
# Installed alongside docker-compose.yml by install.sh; always run this from wherever
# it lives (it cd's to its own directory first, so `cd` beforehand isn't required).
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

COMPOSE="docker compose -f docker-compose.yml"
DB_SERVICE="worldmap_db"
DB_USER="wmap"
DB_NAME="worldmap"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
die()   { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: ./worldmap.sh <command>

Commands:
  start            Pull the latest images and start everything
  stop             Stop and remove all containers
  restart          Restart every service in place
  update           Pull the latest images and restart (same as start)
  status           Show ship/lightning counts per region
  logs [service]   Tail logs (all services, or just one)
  logs save        Save all logs to a timestamped file, with API keys/secrets redacted

Examples:
  ./worldmap.sh start
  ./worldmap.sh logs layer_builder
  ./worldmap.sh logs save
EOF
}

cmd_start() {
    $COMPOSE pull
    $COMPOSE up -d
    info "Running. Live globe: http://localhost:8180  Settings: http://localhost:9000/config"
}

cmd_stop() {
    $COMPOSE down
}

cmd_restart() {
    $COMPOSE restart
}

cmd_status() {
    [ -f .env ] || die "No .env found -- run this from your worldmap-ng install directory."
    echo "--- Ships Located in Each Region ---"
    $COMPOSE exec -T "$DB_SERVICE" psql -U "$DB_USER" "$DB_NAME" -c \
        "SELECT r.label AS region, count(s.mmsi) AS ships
         FROM map_region r
         LEFT JOIN ships s ON ST_Within(s.geom, r.boundary)
         GROUP BY r.label
         ORDER BY ships DESC;"
    echo
    echo "--- Lightning Strikes in Each Region ---"
    $COMPOSE exec -T "$DB_SERVICE" psql -U "$DB_USER" "$DB_NAME" -c \
        "SELECT r.label AS region, count(l.id) AS strikes
         FROM map_region r
         LEFT JOIN lightning_strikes l ON ST_Within(l.geom, r.boundary)
         GROUP BY r.label
         ORDER BY strikes DESC;"
}

# Redacts any secret-looking value from .env (a key ending in API_KEY/TOKEN/PASSWORD/
# SECRET with a non-empty value) out of the given file, in place. Escapes sed/regex
# metacharacters in the secret value itself so odd characters in a key can't break the
# substitution or leak partially.
redact_secrets() {
    local target="$1"
    [ -f .env ] || return 0
    local sed_args=()
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        [[ "$key" =~ (API_KEY|TOKEN|PASSWORD|SECRET)$ ]] || continue
        [ -n "$value" ] || continue
        local escaped
        escaped=$(printf '%s' "$value" | sed -e 's/[\/&]/\\&/g')
        sed_args+=(-e "s/${escaped}/[REDACTED]/g")
    done < <(grep -v '^\s*#' .env | grep '=')

    if [ "${#sed_args[@]}" -gt 0 ]; then
        sed "${sed_args[@]}" "$target" > "$target.redacted"
        mv "$target.redacted" "$target"
    fi
}

cmd_logs() {
    if [ "${1:-}" = "save" ]; then
        local outfile="worldmap-logs-$(date +%Y%m%d-%H%M%S).txt"
        $COMPOSE logs --no-color --timestamps > "$outfile" 2>&1 || true
        redact_secrets "$outfile"
        info "Saved (secrets redacted) to: $outfile"
        echo "Attach this file if you're reporting an issue."
        return
    fi
    $COMPOSE logs -f "$@"
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    update)  cmd_start ;;
    status)  cmd_status ;;
    logs)    shift; cmd_logs "$@" ;;
    -h|--help|help|"") usage ;;
    *) echo "Unknown command: $1" >&2; usage; exit 1 ;;
esac
