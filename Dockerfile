# =============================================================================
# AgentCore — Dockerfile (production build)
#
# Multi-stage:
#   1. node  → builds the React console (console/dist)
#   2. uv    → provides uv binary for fast Python dep installation
#   3. runtime → Python backend + console dist, serves everything
#
# Usage:
#   docker build -t agentcore .
#   docker run -p 8000:8000 -v agentcore-data:/app/.agentcore agentcore
#
# Sub-path deployment (K8s multi-instance):
#   docker run -e AGENTCORE_BASE_PATH=/instance01 -p 8000:8000 agentcore
#
# Build args:
#   AGENTCORE_PORT          Container listen port (default 8000)
#   AGENTCORE_BASE_PATH     URL prefix for K8s multi-instance (e.g. /instance01)
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Frontend build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /build

COPY console/package.json console/package-lock.json ./
RUN npm ci --ignore-scripts

COPY console/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — uv binary (for fast pip replacement)
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:latest AS uv-src

# ---------------------------------------------------------------------------
# Stage 3 — Runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc libffi-dev git curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source tree
COPY src/ src/
COPY pyproject.toml README.md ./

# Copy built frontend from stage 1
COPY --from=frontend /build/dist /app/console/dist/

# Copy uv from stage 2
COPY --from=uv-src /uv /bin/uv

# Install dependencies with uv (much faster than pip)
RUN uv pip install --no-cache-dir --system .

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    AGENTCORE_DATA_DIR=/app/.agentcore \
    AGENTCORE_PORT=8000

# AGENTCORE_BASE_PATH: set at build time or runtime.
# Runtime env takes precedence for K8s multi-instance flexibility.
ARG AGENTCORE_BASE_PATH=""
ENV AGENTCORE_BASE_PATH=${AGENTCORE_BASE_PATH}

# Expose port
EXPOSE ${AGENTCORE_PORT}

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${AGENTCORE_PORT}/health || exit 1

# Default: run uvicorn
CMD ["sh", "-c", "uvicorn agentcore.api:app --host 0.0.0.0 --port ${AGENTCORE_PORT} --workers 1"]
