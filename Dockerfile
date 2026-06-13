# syntax=docker/dockerfile:1
# Base image bundles Playwright + system browsers.
FROM mcr.microsoft.com/playwright:v1.52.0-noble

# uv for fast, reproducible dependency resolution.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

USER root
WORKDIR /app

SHELL ["/bin/bash", "-c"]

ENV DISPLAY=:99 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Copy the project (src-layout) so `uv sync` can install the package itself.
COPY pyproject.toml uv.lock* ./
COPY src/ ./src/
COPY README.md ./

# Install Python dependencies (project installs itself in editable mode).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-cache --all-extras

# System packages: xvfb for headless rendering, tini as PID 1, helpers.
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        xvfb \
        tini \
        wget \
        curl \
        unzip && \
    rm -rf /var/lib/apt/lists/*

# Pre-fetch the Camoufox browser binary (skips if already present).
RUN uv run camoufox fetch

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["xvfb-run", "--auto-servernum", "--server-num=1", \
     "--server-args=-screen 0, 1920x1080x24", \
     "uv", "run", "epic-free"]
