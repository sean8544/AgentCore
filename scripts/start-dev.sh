#!/bin/bash
# =============================================================================
# AgentCore — 开发环境启动脚本 (Linux/Mac)
#
# 启动支持热重载的开发环境。
#
# Usage:
#   ./scripts/start-dev.sh                    # 启动后端（热重载）
#   ./scripts/start-dev.sh --with-postgres    # 启动后端 + PostgreSQL
#   ./scripts/start-dev.sh --frontend-only    # 只启动前端 dev server
#   ./scripts/start-dev.sh --full             # 启动完整开发环境
#   ./scripts/start-dev.sh --stop             # 停止开发环境
# =============================================================================

set -e

# --- Parse arguments ---
WITH_POSTGRES=false
FRONTEND_ONLY=false
FULL=false
STOP=false

for arg in "$@"; do
    case $arg in
        --with-postgres) WITH_POSTGRES=true ;;
        --frontend-only) FRONTEND_ONLY=true ;;
        --full) FULL=true ;;
        --stop) STOP=true ;;
    esac
done

echo "========================================"
echo "AgentCore Development Environment"
echo "========================================"
echo ""

# --- Stop mode ---
if [ "$STOP" = true ]; then
    echo "Stopping development environment..."
    docker compose -f docker-compose.dev.yml down
    echo "Development environment stopped."
    exit 0
fi

# --- Check Docker ---
echo "Checking Docker..."
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi
echo "Docker is running."

# --- Frontend only mode ---
if [ "$FRONTEND_ONLY" = true ]; then
    echo ""
    echo "Starting frontend dev server..."
    echo "  Note: Make sure backend is running separately!"
    echo ""
    
    cd console
    npm run dev
    exit 0
fi

# --- Build dev image (if needed) ---
echo ""
echo "Building development image..."
docker compose -f docker-compose.dev.yml build agentcore-dev

# --- Start services ---
echo ""
echo "Starting development services..."

if [ "$WITH_POSTGRES" = true ] || [ "$FULL" = true ]; then
    echo "  Starting PostgreSQL..."
    docker compose -f docker-compose.dev.yml up -d postgres
    
    # Wait for PostgreSQL
    echo "  Waiting for PostgreSQL to be ready..."
    sleep 10
fi

echo "  Starting AgentCore backend (with hot-reload)..."
docker compose -f docker-compose.dev.yml up -d agentcore-dev

# Wait for backend
echo "  Waiting for backend to start..."
sleep 5

# --- Check status ---
echo ""
echo "========================================"
echo "Development Environment Status"
echo "========================================"
echo ""

docker compose -f docker-compose.dev.yml ps

echo ""
echo "========================================"
echo "Development URLs"
echo "========================================"
echo ""
echo "Backend API:  http://localhost:8000"
echo "Frontend:     http://localhost:8000 (served by backend)"
echo ""
echo "========================================"
echo "Hot Reload Info"
echo "========================================"
echo ""
echo "Backend (Python):"
echo "  - Code changes in src/ will auto-reload"
echo "  - Check: docker compose -f docker-compose.dev.yml logs -f agentcore-dev"
echo ""
echo "Frontend (React):"
echo "  - Option 1: Run 'npm run dev' in console/ directory locally"
echo "  - Option 2: Rebuild container after changes"
echo "    docker compose -f docker-compose.dev.yml up -d --build agentcore-dev"
echo ""
echo "========================================"
echo "Commands"
echo "========================================"
echo ""
echo "View logs:     docker compose -f docker-compose.dev.yml logs -f"
echo "Restart:       docker compose -f docker-compose.dev.yml restart agentcore-dev"
echo "Stop:          ./scripts/start-dev.sh --stop"
echo "Rebuild:       docker compose -f docker-compose.dev.yml up -d --build"
echo ""
