# =============================================================================
# AgentCore Development Environment Startup Script (Windows)
#
# Usage:
#   .\scripts\start-dev.ps1                    # Start backend (hot-reload)
#   .\scripts\start-dev.ps1 -WithPostgres      # Start backend + PostgreSQL
#   .\scripts\start-dev.ps1 -FrontendOnly      # Start frontend dev server only
#   .\scripts\start-dev.ps1 -Full              # Start full dev environment
#   .\scripts\start-dev.ps1 -Stop              # Stop dev environment
# =============================================================================

param(
    [switch]$WithPostgres,
    [switch]$FrontendOnly,
    [switch]$Full,
    [switch]$Stop
)

# Note: Do not set $ErrorActionPreference = Stop globally
# because docker commands may output warnings that trigger it.

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "AgentCore Development Environment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Stop mode ---
if ($Stop) {
    Write-Host "Stopping development environment..." -ForegroundColor Yellow
    docker compose -f docker-compose.dev.yml down
    Write-Host "Development environment stopped." -ForegroundColor Green
    exit 0
}

# --- Check Docker ---
Write-Host "Checking Docker..." -ForegroundColor Yellow
$dockerCheck = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Docker is not running." -ForegroundColor Red
    Write-Host "  Please start Docker Desktop / Rancher Desktop first." -ForegroundColor Gray
    exit 1
}
Write-Host "Docker is running." -ForegroundColor Green

# --- Frontend only mode ---
if ($FrontendOnly) {
    Write-Host ""
    Write-Host "Starting frontend dev server..." -ForegroundColor Yellow
    Write-Host "  Note: Make sure backend is running separately!" -ForegroundColor Gray
    Write-Host ""
    
    Set-Location console
    npm run dev
    exit 0
}

# --- Build dev image (if needed) ---
Write-Host ""
Write-Host "Building development image..." -ForegroundColor Yellow
docker compose -f docker-compose.dev.yml build agentcore-dev
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to build development image." -ForegroundColor Red
    exit 1
}
Write-Host "Development image built." -ForegroundColor Green

# --- Start services ---
Write-Host ""
Write-Host "Starting development services..." -ForegroundColor Yellow

if ($WithPostgres -or $Full) {
    Write-Host "  Starting PostgreSQL..." -ForegroundColor Gray
    docker compose -f docker-compose.dev.yml up -d postgres
    
    # Wait for PostgreSQL
    Write-Host "  Waiting for PostgreSQL to be ready..." -ForegroundColor Gray
    Start-Sleep -Seconds 10
}

Write-Host "  Starting AgentCore backend (with hot-reload)..." -ForegroundColor Gray
docker compose -f docker-compose.dev.yml up -d agentcore-dev

# Wait for backend
Write-Host "  Waiting for backend to start..." -ForegroundColor Gray
Start-Sleep -Seconds 5

# --- Check status ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Development Environment Status" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

docker compose -f docker-compose.dev.yml ps

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Development URLs" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Backend API:  http://localhost:8000" -ForegroundColor White
Write-Host "Frontend:     http://localhost:8000 (served by backend)" -ForegroundColor White
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Hot Reload Info" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Backend (Python):" -ForegroundColor Yellow
Write-Host "  - Code changes in src/ will auto-reload" -ForegroundColor White
Write-Host "  - Check: docker compose -f docker-compose.dev.yml logs -f agentcore-dev" -ForegroundColor Gray
Write-Host ""
Write-Host "Frontend (React):" -ForegroundColor Yellow
Write-Host "  - Option 1: Run 'npm run dev' in console/ directory locally" -ForegroundColor White
Write-Host "  - Option 2: Rebuild container after changes" -ForegroundColor White
Write-Host "    docker compose -f docker-compose.dev.yml up -d --build agentcore-dev" -ForegroundColor Gray
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Commands" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "View logs:     docker compose -f docker-compose.dev.yml logs -f" -ForegroundColor White
Write-Host "Restart:       docker compose -f docker-compose.dev.yml restart agentcore-dev" -ForegroundColor White
Write-Host "Stop:          .\scripts\start-dev.ps1 -Stop" -ForegroundColor White
Write-Host "Rebuild:       docker compose -f docker-compose.dev.yml up -d --build" -ForegroundColor White
Write-Host ""
