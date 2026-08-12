FROM python:3.12-slim

# Define build arguments for host UID/GID mapping (defaults to 1000)
ARG UID=1000
ARG GID=1000

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
    libeccodes-dev \
    procps \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# Relax ImageMagick security policy
RUN sed -i 's/domain="coder" rights="none" pattern="PDF"/domain="coder" rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml || true

# Localization
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && echo 'en_NZ.UTF-8 UTF-8' > /etc/locale.gen && locale-gen en_NZ.UTF-8

# Create the non-root user and group
RUN groupadd -g ${GID} aglgroup && \
    useradd -u ${UID} -g aglgroup -m agluser

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
COPY src/ ./src/
COPY ui/images/ ./images/
COPY markers/ ./markers/
COPY alembic.ini ./
COPY alembic/ ./alembic/

# 6. Final Sync & Script Installation
RUN uv sync --frozen --no-dev --editable \
    && uv pip install -e .

# Grant the non-root user ownership of both the project and the virtual environment
RUN chown -R agluser:aglgroup /opt/project /opt/venv

# map_api installs a few extra packages into /opt/venv at container startup (see its
# command in docker-compose.yml). In prod, that container may run as an arbitrary host
# UID/GID (docker-compose.yml's `user:` override, matching bind-mounted ./data and
# ./config's real host ownership) rather than agluser's build-time UID -- so /opt/venv
# needs to stay writable regardless of which UID ends up owning the process. Directories
# get +x (traversable/writable), files just +w (X only applies execute to dirs/already-
# executable files, never turns a data file executable).
RUN chmod -R o+wX /opt/venv

# Switch to the non-root user
USER agluser

# 7. Runtime Configuration
ENV PYTHONPATH="/opt/project/src"

# Updated fallback command to use the new 'builder' script entry point
CMD ["builder", "--config", "config/atmos-gl.json"]