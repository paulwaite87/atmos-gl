FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Added procps for basic process management if you ever need to debug
RUN apt-get update -y \
    && apt-get -y install --no-install-recommends \
    locales curl imagemagick ca-certificates xplanet xplanet-images libeccodes-dev procps \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# Set timezone and locale
ENV TZ=Pacific/Auckland
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && echo 'en_NZ.UTF-8 UTF-8' > /etc/locale.gen && locale-gen en_NZ.UTF-8
ENV LANG=en_NZ.UTF-8

# Use UV for fast, reproducible dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /opt/project
ENV PATH="/opt/project/.venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy project files and set permissions
COPY . .
RUN chmod +x *.sh && uv sync --frozen --no-dev

# The daemon manages the shift between World map rendering and Ship data harvesting
CMD [ "python", "map_system_daemon.py", \
    "--map-updater", "./do_map_updates.sh", "--update-sleep", "120", \
    "--harvester", "./do_harvest_shipdata.sh", "--harvest-sleep", "600", \
    "--morning", "09:00", \
    "--evening", "23:00" \
]