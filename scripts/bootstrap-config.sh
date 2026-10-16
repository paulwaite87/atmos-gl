#!/usr/bin/env bash
# Shared by Makefile (dev) and Makefile.prod's `bootstrap-config` targets. Ensures the
# live config/terrascope.json and .env exist, and keeps terrascope.json's shape synced with
# its template (tools/sync_config.py) -- the single place this logic lives now, so it
# can never again exist only in one Makefile and silently miss the other, the way
# Makefile.prod's own copy of this target never picked up the config-sync step at all.
#
# Pass --with-override to also bootstrap docker-compose.override.yml (dev only --
# prod deliberately has no override-file concept).
set -euo pipefail

if [ ! -f config/terrascope.json ]; then
    cp config/terrascope.json.tmpl config/terrascope.json
    echo "Created config/terrascope.json from config/terrascope.json.tmpl"
else
    python3 tools/sync_config.py
fi

if [ ! -f .env ]; then
    cp .env.tmpl .env
    echo "Created .env from .env.tmpl -- edit this to add your API keys"
fi

if [ "${1:-}" = "--with-override" ] && [ ! -f docker-compose.override.yml ]; then
    cp docker-compose.override.yml.tmpl docker-compose.override.yml
    echo "Created docker-compose.override.yml from docker-compose.override.yml.tmpl"
fi
