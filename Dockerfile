FROM python:3.12-slim

# 1. Environment & Global Settings
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Pacific/Auckland \
    LANG=en_NZ.UTF-8

# 2. System Dependencies
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

# Relax ImageMagick security policy
RUN sed -i 's/domain="coder" rights="none" pattern="PDF"/domain="coder" rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml || true

# Localization
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && echo 'en_NZ.UTF-8 UTF-8' > /etc/locale.gen && locale-gen en_NZ.UTF-8

# 3. Virtual Environment & Tooling Setup
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Move the venv OUTSIDE the project root so it isn't overwritten by host volumes
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /opt/project

# 4. Dependency Installation (Cached Layer)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# 5. Application Code & Assets
# Note: Copy the whole src directory to ensure the 'worldmap' package is findable
COPY src/ ./src/
COPY images/ ./images/
COPY markers/ ./markers/

# 6. Final Sync & Script Installation
# This ensures all dependencies are synced AND the
# scripts (harvester, builder) are created in /opt/venv/bin/
RUN uv sync --frozen --no-dev --editable \
    && uv pip install -e .

# 7. Runtime Configuration
ENV PYTHONPATH="/opt/project/src"

# Updated fallback command to use the new 'builder' script entry point
CMD ["builder", "--config", "config/worldmap.conf"]