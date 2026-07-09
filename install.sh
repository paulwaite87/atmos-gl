#!/usr/bin/env bash
# Atmos GL installer -- downloads everything needed to run the pre-built atmos-gl
# stack via Docker Compose, without needing a full git clone.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/paulwaite87/atmos-gl/master/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/paulwaite87/atmos-gl/master/install.sh | bash -s -- /path/to/atmos-gl
#
# Safe to re-run: refreshes docker-compose.yml, atmos-gl.sh and the markers/ reference
# data, but never overwrites an existing .env or config/atmos-gl.json -- those are your
# live, locally-customised files.
#
# Note for the maintainer: the repo was renamed worldmap-ng -> atmos-gl, so the
# published images moved to new GHCR packages (ghcr.io/paulwaite87/atmos-gl and
# -ui). A freshly-created GHCR package can start out private; if `docker compose
# pull` fails with "denied" for someone running this script, check that both
# packages are set to public under https://github.com/paulwaite87?tab=packages.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/paulwaite87/atmos-gl/master"
INSTALL_DIR="${1:-$HOME/atmos-gl}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn()  { printf '\033[1;33m!!\033[0m %s\n' "$1"; }
die()   { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "Docker is not installed. See https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is not available (try: docker compose version)."

# fetch <repo-relative-path> <local-path> -- always overwrites; only call this for
# static/reference files, never for the user's live .env or config/atmos-gl.json.
fetch() {
    local src="$1" dest="$2"
    curl -fsSL "$REPO_RAW/$src" -o "$dest" || die "Failed to download $src"
}

# copy_if_missing <local-template-path> <local-live-path> <message> -- only creates
# the live file (from an already-fetched local template) if it doesn't exist yet, so a
# re-run never clobbers something the user has customised.
copy_if_missing() {
    local tmpl="$1" dest="$2"
    if [ -f "$dest" ]; then
        info "Keeping existing $dest (not overwritten)"
    else
        cp "$tmpl" "$dest"
        echo "$3"
    fi
}

info "Installing Atmos GL into $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"/config "$INSTALL_DIR"/markers "$INSTALL_DIR"/data
cd "$INSTALL_DIR"

info "Fetching docker-compose.yml and atmos-gl.sh"
fetch docker-compose.yml docker-compose.yml
fetch atmos-gl.sh atmos-gl.sh
chmod +x atmos-gl.sh

info "Fetching marker reference data"
fetch markers/base_markers_global.txt markers/base_markers_global.txt
fetch markers/base_markers_regional.txt markers/base_markers_regional.txt
fetch markers/extra_world_markers.txt markers/extra_world_markers.txt
fetch markers/markers.geojson markers/markers.geojson

info "Fetching config templates"
fetch .env.tmpl .env.tmpl
fetch config/atmos-gl.json.tmpl config/atmos-gl.json.tmpl

copy_if_missing .env.tmpl .env "Created .env -- edit this to add your API keys (see the README's API key sections)."
copy_if_missing config/atmos-gl.json.tmpl config/atmos-gl.json "Created config/atmos-gl.json -- most layers start disabled; enable some via http://localhost:9000/config"

# The published image runs as a fixed non-root UID baked in at build time, which won't
# necessarily match your host user -- make the bind-mounted folders writable by anyone
# rather than requiring a matching UID/GID.
chmod -R a+rwX data config markers

echo
info "Install complete."
echo
echo "  1. Edit .env and add your API keys (Shipping/Lightning are optional; Map tiles is not)."
echo "  2. Run:  cd $INSTALL_DIR && ./atmos-gl.sh start"
echo "  3. Browse to http://localhost:8180 for the live globe,"
echo "     and http://localhost:9000/config to enable layers."
echo
echo "Run ./atmos-gl.sh with no arguments any time for the full command list."
