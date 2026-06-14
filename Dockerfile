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
RUN groupadd -g ${GID} wmapgroup && \
    useradd -u ${UID} -g wmapgroup -m wmapuser

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

# 6. Final Sync & Script Installation
RUN uv sync --frozen --no-dev --editable \
    && uv pip install -e .

# Grant the non-root user ownership of both the project and the virtual environment
RUN chown -R wmapuser:wmapgroup /opt/project /opt/venv

# Switch to the non-root user
USER wmapuser

# 7. Runtime Configuration
ENV PYTHONPATH="/opt/project/src"

# Updated fallback command to use the new 'builder' script entry point
CMD ["builder", "--config", "config/worldmap.json"]