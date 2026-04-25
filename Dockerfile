FROM python:3.12-slim

# Prevent Python from writing .pyc files and ensure logs are flushed immediately
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
# Added procps for debugging and ensured ca-certificates are up to date
RUN apt-get update -y \
    && apt-get -y install --no-install-recommends \
    locales \
    curl \
    imagemagick \
    ca-certificates \
    xplanet \
    xplanet-images \
    libeccodes-dev \
    procps \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# Set timezone and locale to New Zealand
ENV TZ=Pacific/Auckland
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && echo 'en_NZ.UTF-8 UTF-8' > /etc/locale.gen && locale-gen en_NZ.UTF-8
ENV LANG=en_NZ.UTF-8

# Install UV for high-performance dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /opt/project

# Setup the virtual environment path
ENV PATH="/opt/project/.venv/bin:$PATH"

# 1. Install dependencies (Layer Caching)
# We copy only the lockfiles first so that code changes don't trigger a re-download of packages
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy system content
COPY src/ ./src/
COPY images/ ./images/
COPY markers/ ./markers/

# 3. Finalize installation
# This installs the project itself into the venv
RUN uv sync --frozen --no-dev

# 4. Set Python Path
# This is CRITICAL: it allows 'python -m worldmap.daemon' to find the package inside /src
ENV PYTHONPATH="/opt/project/src"

# The daemon manages the shift between World map rendering and Ship data harvesting.
# It handles SIGTERM and SIGINT for graceful shutdowns in Docker.
CMD ["python", "-m", "worldmap.daemon", "--config", "config/worldmap.conf"]