# =============================================================================
# AgentCore — 本地启动 PostgreSQL 脚本 (Windows)
#
# 使用 Docker 在本地启动 PostgreSQL 实例，用于开发和测试。
#
# Usage:
#   .\scripts\start_postgres.ps1                    # 使用默认配置
#   .\scripts\start_postgres.ps1 -Port 5433         # 自定义端口
#   .\scripts\start_postgres.ps1 -Password mypass   # 自定义密码
#   .\scripts\start_postgres.ps1 -Remove            # 删除容器和数据
# =============================================================================

param(
    [string]$ContainerName = "agentcore-postgres",
    [string]$Port = "5432",
    [string]$Password = "agentcore_secret",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "AgentCore PostgreSQL Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Remove mode ---
if ($Remove) {
    Write-Host "Removing PostgreSQL container and data..." -ForegroundColor Yellow
    
    $existing = docker ps -a --filter "name=$ContainerName" --format "{{.Names}}" 2>$null
    if ($existing) {
        docker stop $ContainerName 2>$null
        docker rm $ContainerName 2>$null
        Write-Host "Container '$ContainerName' removed." -ForegroundColor Green
    } else {
        Write-Host "Container '$ContainerName' not found." -ForegroundColor Yellow
    }
    
    # Remove volume
    docker volume rm agentcore-pgdata 2>$null
    Write-Host "Volume 'agentcore-pgdata' removed." -ForegroundColor Green
    
    Write-Host ""
    Write-Host "PostgreSQL cleanup complete." -ForegroundColor Green
    exit 0
}

# --- Check Docker ---
Write-Host "Checking Docker..." -ForegroundColor Yellow
try {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not running"
    }
} catch {
    Write-Host "Error: Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
    exit 1
}
Write-Host "Docker is running." -ForegroundColor Green

# --- Check/Create container ---
Write-Host ""
Write-Host "Checking PostgreSQL container..." -ForegroundColor Yellow

$existing = docker ps -a --filter "name=$ContainerName" --format "{{.Names}}" 2>$null
if ($existing) {
    Write-Host "Container '$ContainerName' already exists." -ForegroundColor Yellow
    
    # Check if running
    $running = docker ps --filter "name=$ContainerName" --format "{{.Names}}" 2>$null
    if ($running) {
        Write-Host "Container is already running." -ForegroundColor Green
    } else {
        Write-Host "Starting existing container..." -ForegroundColor Yellow
        docker start $ContainerName
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Error: Failed to start container." -ForegroundColor Red
            exit 1
        }
        Write-Host "Container started." -ForegroundColor Green
    }
} else {
    Write-Host "Creating new PostgreSQL container..." -ForegroundColor Green
    
    docker run -d `
        --name $ContainerName `
        -e POSTGRES_DB=agentcore `
        -e POSTGRES_USER=agentcore `
        -e POSTGRES_PASSWORD=$Password `
        -p "${Port}:5432" `
        -v agentcore-pgdata:/var/lib/postgresql/data `
        --restart unless-stopped `
        postgres:16-alpine
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Failed to create container." -ForegroundColor Red
        exit 1
    }
    Write-Host "Container created." -ForegroundColor Green
}

# --- Wait for PostgreSQL ---
Write-Host ""
Write-Host "Waiting for PostgreSQL to be ready..." -ForegroundColor Yellow

$retries = 30
$ready = $false
while ($retries -gt 0) {
    docker exec $ContainerName pg_isready -U agentcore 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
    $retries--
}

if (-not $ready) {
    Write-Host "Error: PostgreSQL failed to start within 30 seconds." -ForegroundColor Red
    exit 1
}

Write-Host "PostgreSQL is ready!" -ForegroundColor Green

# --- Output configuration ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "PostgreSQL Configuration" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Host:       localhost" -ForegroundColor White
Write-Host "Port:       $Port" -ForegroundColor White
Write-Host "Database:   agentcore" -ForegroundColor White
Write-Host "User:       agentcore" -ForegroundColor White
Write-Host "Password:   $Password" -ForegroundColor White
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Add to .env file:" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "AGENTCORE_CHECKPOINTER_BACKEND=postgresql" -ForegroundColor Yellow
Write-Host "AGENTCORE_POSTGRES_HOST=localhost" -ForegroundColor Yellow
Write-Host "AGENTCORE_POSTGRES_PORT=$Port" -ForegroundColor Yellow
Write-Host "AGENTCORE_POSTGRES_DB=agentcore" -ForegroundColor Yellow
Write-Host "AGENTCORE_POSTGRES_USER=agentcore" -ForegroundColor Yellow
Write-Host "AGENTCORE_POSTGRES_PASSWORD=$Password" -ForegroundColor Yellow
Write-Host ""
Write-Host "Or use a single connection string:" -ForegroundColor Yellow
Write-Host "AGENTCORE_POSTGRES_URL=postgresql://agentcore:${Password}@localhost:${Port}/agentcore" -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Install PostgreSQL support:" -ForegroundColor White
Write-Host "   pip install agentcore[postgresql]" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Update .env file with the configuration above" -ForegroundColor White
Write-Host ""
Write-Host "3. Start AgentCore:" -ForegroundColor White
Write-Host "   uvicorn agentcore.api:app --host 0.0.0.0 --port 8000" -ForegroundColor Gray
Write-Host ""
