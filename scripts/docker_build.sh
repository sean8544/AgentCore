#!/usr/bin/env bash
# =============================================================================
# AgentCore — Docker Build Script
#
# Usage:
#   bash scripts/docker_build.sh [IMAGE_TAG] [EXTRA_ARGS...]
#
# Examples:
#   bash scripts/docker_build.sh                          # → agentcore:latest
#   bash scripts/docker_build.sh agentcore:v0.1.0         # → agentcore:v0.1.0
#   bash scripts/docker_build.sh myreg/agentcore:v1 --no-cache
#
# Sub-path deployment (K8s):
#   AGENTCORE_BASE_PATH=/instance01 bash scripts/docker_build.sh
#
# Environment variables:
#   AGENTCORE_BASE_PATH   URL prefix for K8s multi-instance (e.g. /instance01)
#   AGENTCORE_PORT        Container listen port (default: 8000)
# =============================================================================
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG="${1:-agentcore:latest}"
shift || true

# Read version from pyproject.toml
AGENTCORE_VERSION=$(grep -E '^version\s*=' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
if [ -z "$AGENTCORE_VERSION" ]; then
    echo "[docker_build] WARNING: Could not read version from pyproject.toml"
    AGENTCORE_VERSION="dev"
fi

# Build args
BUILD_ARGS=(
    --build-arg "AGENTCORE_VERSION=$AGENTCORE_VERSION"
)

# Optional: sub-path deployment prefix
if [ -n "$AGENTCORE_BASE_PATH" ]; then
    echo "[docker_build] Sub-path deployment: $AGENTCORE_BASE_PATH"
    BUILD_ARGS+=(--build-arg "AGENTCORE_BASE_PATH=$AGENTCORE_BASE_PATH")
fi

# Optional: port override
if [ -n "$AGENTCORE_PORT" ]; then
    BUILD_ARGS+=(--build-arg "AGENTCORE_PORT=$AGENTCORE_PORT")
fi

echo "[docker_build] Building image: $TAG"
echo "[docker_build] AgentCore version: $AGENTCORE_VERSION"

docker build \
    "${BUILD_ARGS[@]}" \
    -t "$TAG" \
    "$@" \
    "$REPO_ROOT"

echo ""
echo "[docker_build] Done."
echo "[docker_build] Image: $TAG"
echo "[docker_build]"
echo "[docker_build] Run:"
echo "[docker_build]   docker run -p 8000:8000 -v agentcore-data:/app/.agentcore $TAG"
echo "[docker_build]"
echo "[docker_build] Sub-path example:"
echo "[docker_build]   docker run -e AGENTCORE_BASE_PATH=/instance01 -p 8000:8000 $TAG"
echo "[docker_build]"
echo "[docker_build] Or use docker compose:"
echo "[docker_build]   docker compose up -d"
