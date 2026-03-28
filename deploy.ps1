# ============================================================
#  Meeting MOM Generator - Docker Deployment Script
#  Run this from the project root directory in PowerShell
# ============================================================

param(
    [ValidateSet("up", "down", "restart", "logs", "rebuild", "status")]
    [string]$Action = "up"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "`n>>> $msg" -ForegroundColor Cyan
}

# Pre-flight checks
function Test-Prerequisites {
    Write-Step "Checking prerequisites"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: Docker is not installed or not in PATH." -ForegroundColor Red
        Write-Host "Install Docker Desktop from https://docs.docker.com/desktop/install/windows-install/"
        exit 1
    }

    if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue) -and
        -not (docker compose version 2>$null)) {
        Write-Host "ERROR: docker-compose is not available." -ForegroundColor Red
        exit 1
    }

    if (-not (Test-Path ".env")) {
        Write-Host "ERROR: .env file not found." -ForegroundColor Red
        Write-Host "Copy .env.example to .env and fill in your values:"
        Write-Host "  Copy-Item .env.example .env"
        exit 1
    }

    Write-Host "All prerequisites met." -ForegroundColor Green
}

switch ($Action) {
    "up" {
        Test-Prerequisites
        Write-Step "Building and starting containers"
        docker compose up -d --build
        Write-Host ""
        Write-Host "============================================" -ForegroundColor Green
        Write-Host "  App is running at http://localhost:5100"     -ForegroundColor Green
        Write-Host "============================================" -ForegroundColor Green
        Write-Host ""
        docker compose ps
    }

    "down" {
        Write-Step "Stopping and removing containers"
        docker compose down
        Write-Host "Containers stopped." -ForegroundColor Yellow
    }

    "restart" {
        Write-Step "Restarting containers"
        docker compose down
        docker compose up -d --build
        Write-Host ""
        Write-Host "App restarted at http://localhost:5100" -ForegroundColor Green
        docker compose ps
    }

    "rebuild" {
        Write-Step "Rebuilding from scratch (no cache)"
        docker compose down
        docker compose build --no-cache
        docker compose up -d
        Write-Host ""
        Write-Host "App rebuilt and running at http://localhost:5100" -ForegroundColor Green
        docker compose ps
    }

    "logs" {
        Write-Step "Showing live logs (Ctrl+C to exit)"
        docker compose logs -f --tail 100
    }

    "status" {
        Write-Step "Container status"
        docker compose ps
        Write-Host ""
        Write-Step "Recent app logs"
        docker compose logs --tail 20 app
    }
}
