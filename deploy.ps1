# ============================================================
#  Meeting MOM Generator - Docker Deployment Script
#  
#  Usage:
#    .\deploy.ps1              # First-time setup + start
#    .\deploy.ps1 -Action up   # Start containers
#    .\deploy.ps1 -Action down # Stop containers
#    .\deploy.ps1 -Action restart
#    .\deploy.ps1 -Action rebuild
#    .\deploy.ps1 -Action logs
#    .\deploy.ps1 -Action status
# ============================================================

param(
    [ValidateSet("up", "down", "restart", "logs", "rebuild", "status")]
    [string]$Action = "up"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host ">>> $msg" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor DarkGray
}

function Write-Success($msg) {
    Write-Host $msg -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host $msg -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
}

# ── Pre-flight checks ────────────────────────────────────────

function Test-Prerequisites {
    Write-Step "Checking prerequisites"

    # Docker installed?
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Err "Docker is not installed or not in PATH."
        Write-Host "  Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
        exit 1
    }
    Write-Success "  [OK] Docker found"

    # Docker running?
    try {
        docker info *>$null
        Write-Success "  [OK] Docker daemon is running"
    } catch {
        Write-Err "Docker daemon is not running. Start Docker Desktop first."
        exit 1
    }

    # Docker Compose available?
    $composeAvailable = $false
    try {
        docker compose version *>$null
        $composeAvailable = $true
    } catch {}
    if (-not $composeAvailable) {
        try {
            docker-compose version *>$null
            $composeAvailable = $true
        } catch {}
    }
    if (-not $composeAvailable) {
        Write-Err "docker compose is not available."
        exit 1
    }
    Write-Success "  [OK] Docker Compose found"

    # .env file exists?
    if (-not (Test-Path ".env")) {
        Write-Warn "  .env file not found. Creating from .env.example..."
        if (Test-Path ".env.example") {
            Copy-Item ".env.example" ".env"
            Write-Warn "  Created .env from .env.example"
            Write-Warn "  IMPORTANT: Edit .env and fill in your actual values before continuing!"
            Write-Host ""
            Write-Host "  Required values to set:" -ForegroundColor White
            Write-Host "    - AZURE_CLIENT_ID"
            Write-Host "    - AZURE_CLIENT_SECRET"
            Write-Host "    - AZURE_TENANT_ID"
            Write-Host "    - FLASK_SECRET_KEY"
            Write-Host "    - POSTGRES_PASSWORD"
            Write-Host "    - REDIRECT_URI (set to your server URL)"
            Write-Host "    - OPENAI_API_KEY"
            Write-Host ""
            Read-Host "  Press Enter after editing .env to continue (or Ctrl+C to cancel)"
        } else {
            Write-Err ".env.example not found either. Cannot proceed."
            exit 1
        }
    }
    Write-Success "  [OK] .env file found"

    # Check port 5100 is free
    $portInUse = Get-NetTCPConnection -LocalPort 5100 -ErrorAction SilentlyContinue
    if ($portInUse) {
        Write-Warn "  Port 5100 is already in use. It may be this app already running."
    } else {
        Write-Success "  [OK] Port 5100 is available"
    }

    Write-Host ""
    Write-Success "All prerequisites met."
}

# ── Actions ──────────────────────────────────────────────────

switch ($Action) {
    "up" {
        Test-Prerequisites

        Write-Step "Building and starting containers"
        docker compose up -d --build

        Write-Host ""
        Write-Host "============================================" -ForegroundColor Green
        Write-Host "  MOM Generator is running on port 5100"      -ForegroundColor Green
        Write-Host "  URL: http://localhost:5100"                  -ForegroundColor Green
        Write-Host "============================================" -ForegroundColor Green
        Write-Host ""

        Write-Step "Container status"
        docker compose ps
    }

    "down" {
        Write-Step "Stopping containers"
        docker compose down
        Write-Warn "Containers stopped."
    }

    "restart" {
        Write-Step "Restarting containers"
        docker compose down
        docker compose up -d --build
        Write-Host ""
        Write-Success "App restarted on port 5100"
        Write-Host ""
        docker compose ps
    }

    "rebuild" {
        Write-Step "Full rebuild (no cache)"
        docker compose down
        docker compose build --no-cache
        docker compose up -d
        Write-Host ""
        Write-Success "App rebuilt and running on port 5100"
        Write-Host ""
        docker compose ps
    }

    "logs" {
        Write-Step "Live logs (Ctrl+C to exit)"
        docker compose logs -f --tail 100
    }

    "status" {
        Write-Step "Container status"
        docker compose ps
        Write-Host ""
        Write-Step "Recent app logs (last 30 lines)"
        docker compose logs --tail 30 mom-app
    }
}
