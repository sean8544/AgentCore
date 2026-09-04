# =============================================================================
# AgentCore - Local Development Startup Script (Windows)
#
# Run backend locally with hot-reload (no Docker needed for backend).
# Only use Docker for PostgreSQL if needed.
#
# Usage:
#   .\scripts\start-local-dev.ps1                  # Start backend only
#   .\scripts\start-local-dev.ps1 -WithPostgres    # Start backend + PostgreSQL via Docker
#   .\scripts\start-local-dev.ps1 -FrontendOnly    # Start frontend dev server only
# =============================================================================

param(
    [switch]$WithPostgres,
    [switch]$FrontendOnly
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "AgentCore Local Development" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Frontend only mode ---
if ($FrontendOnly) {
    Write-Host "Starting frontend dev server..." -ForegroundColor Yellow
    Set-Location console
    npm run dev
    exit 0
}

# --- Start PostgreSQL if needed ---
if ($WithPostgres) {
    Write-Host "Starting PostgreSQL via Docker..." -ForegroundColor Yellow
    
    # Check if postgres container exists
    $pgExists = docker ps -a --filter "name=agentcore-postgres-dev" --format "{{.Names}}" 2>$null
    if ($pgExists) {
        Write-Host "  PostgreSQL container exists, starting..." -ForegroundColor Gray
        docker start agentcore-postgres-dev
    } else {
        Write-Host "  Creating PostgreSQL container..." -ForegroundColor Gray
        docker run -d `
            --name agentcore-postgres-dev `
            -e POSTGRES_DB=agentcore `
            -e POSTGRES_USER=agentcore `
            -e POSTGRES_PASSWORD=agentcore_secret `
            -p 5432:5432 `
            postgres:16-alpine
    }
    
    Write-Host "  Waiting for PostgreSQL to be ready..." -ForegroundColor Gray
    Start-Sleep -Seconds 5
    
    # Set environment variable
    $env:AGENTCORE_CHECKPOINTER_BACKEND = "postgresql"
    $env:AGENTCORE_POSTGRES_HOST = "localhost"
    $env:AGENTCORE_POSTGRES_PORT = "5432"
    $env:AGENTCORE_POSTGRES_DB = "agentcore"
    $env:AGENTCORE_POSTGRES_USER = "agentcore"
    $env:AGENTCORE_POSTGRES_PASSWORD = "agentcore_secret"
    
    Write-Host "PostgreSQL is ready." -ForegroundColor Green
} else {
    Write-Host "Using SQLite (default). Use -WithPostgres for PostgreSQL." -ForegroundColor Gray
}

Write-Host ""
Write-Host "Starting AgentCore backend with hot-reload..." -ForegroundColor Yellow
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

# --- Check if uvicorn is available ---
$uvicornCheck = python -c "import uvicorn" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: uvicorn not found. Installing dependencies..." -ForegroundColor Yellow
    pip install -e ".[dev]"
}

# --- Start uvicorn with hot-reload ---
Set-Location src
python -m uvicorn agentcore.api:app --host 0.0.0.0 --port 8000 --reload --reload-dir .
