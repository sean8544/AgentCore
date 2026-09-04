#!/bin/bash
# =============================================================================
# AgentCore — 本地启动 PostgreSQL 脚本 (Linux/Mac)
#
# 使用 Docker 在本地启动 PostgreSQL 实例，用于开发和测试。
#
# Usage:
#   ./scripts/start_postgres.sh                    # 使用默认配置
#   ./scripts/start_postgres.sh 5433               # 自定义端口
#   ./scripts/start_postgres.sh 5432 mypassword    # 自定义端口和密码
#   ./scripts/start_postgres.sh --remove           # 删除容器和数据
# =============================================================================

set -e

# --- Configuration ---
CONTAINER_NAME="agentcore-postgres"
PORT="${1:-5432}"
PASSWORD="${2:-agentcore_secret}"

# Check for --remove flag
if [ "$1" = "--remove" ] || [ "$2" = "--remove" ]; then
    echo "Removing PostgreSQL container and data..."
    
    if docker ps -a --filter "name=$CONTAINER_NAME" --format "{{.Names}}" | grep -q "$CONTAINER_NAME"; then
        docker stop "$CONTAINER_NAME" 2>/dev/null || true
        docker rm "$CONTAINER_NAME" 2>/dev/null || true
        echo "Container '$CONTAINER_NAME' removed."
    else
        echo "Container '$CONTAINER_NAME' not found."
    fi
    
    docker volume rm agentcore-pgdata 2>/dev/null || true
    echo "Volume 'agentcore-pgdata' removed."
    
    echo ""
    echo "PostgreSQL cleanup complete."
    exit 0
fi

echo "========================================"
echo "AgentCore PostgreSQL Setup"
echo "========================================"
echo ""

# --- Check Docker ---
echo "Checking Docker..."
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi
echo "Docker is running."

# --- Check/Create container ---
echo ""
echo "Checking PostgreSQL container..."

if docker ps -a --filter "name=$CONTAINER_NAME" --format "{{.Names}}" | grep -q "$CONTAINER_NAME"; then
    echo "Container '$CONTAINER_NAME' already exists."
    
    # Check if running
    if docker ps --filter "name=$CONTAINER_NAME" --format "{{.Names}}" | grep -q "$CONTAINER_NAME"; then
        echo "Container is already running."
    else
        echo "Starting existing container..."
        docker start "$CONTAINER_NAME"
        echo "Container started."
    fi
else
    echo "Creating new PostgreSQL container..."
    
    docker run -d \
        --name "$CONTAINER_NAME" \
        -e POSTGRES_DB=agentcore \
        -e POSTGRES_USER=agentcore \
        -e POSTGRES_PASSWORD="$PASSWORD" \
        -p "${PORT}:5432" \
        -v agentcore-pgdata:/var/lib/postgresql/data \
        --restart unless-stopped \
        postgres:16-alpine
    
    echo "Container created."
fi

# --- Wait for PostgreSQL ---
echo ""
echo "Waiting for PostgreSQL to be ready..."

retries=30
ready=false
while [ $retries -gt 0 ]; do
    if docker exec "$CONTAINER_NAME" pg_isready -U agentcore > /dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
    retries=$((retries - 1))
done

if [ "$ready" = false ]; then
    echo "Error: PostgreSQL failed to start within 30 seconds."
    exit 1
fi

echo "PostgreSQL is ready!"

# --- Output configuration ---
echo ""
echo "========================================"
echo "PostgreSQL Configuration"
echo "========================================"
echo ""
echo "Host:       localhost"
echo "Port:       $PORT"
echo "Database:   agentcore"
echo "User:       agentcore"
echo "Password:   $PASSWORD"
echo ""
echo "========================================"
echo "Add to .env file:"
echo "========================================"
echo ""
echo "AGENTCORE_CHECKPOINTER_BACKEND=postgresql"
echo "AGENTCORE_POSTGRES_HOST=localhost"
echo "AGENTCORE_POSTGRES_PORT=$PORT"
echo "AGENTCORE_POSTGRES_DB=agentcore"
echo "AGENTCORE_POSTGRES_USER=agentcore"
echo "AGENTCORE_POSTGRES_PASSWORD=$PASSWORD"
echo ""
echo "Or use a single connection string:"
echo "AGENTCORE_POSTGRES_URL=postgresql://agentcore:${PASSWORD}@localhost:${PORT}/agentcore"
echo ""
echo "========================================"
echo "Next steps:"
echo "========================================"
echo ""
echo "1. Install PostgreSQL support:"
echo "   pip install agentcore[postgresql]"
echo ""
echo "2. Update .env file with the configuration above"
echo ""
echo "3. Start AgentCore:"
echo "   uvicorn agentcore.api:app --host 0.0.0.0 --port 8000"
echo ""
