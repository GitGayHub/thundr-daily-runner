# =====================================================================
# Thundr Daily Rewards Runner - Windows PowerShell Setup Script
# =====================================================================
[CmdletBinding()]
param (
    [switch]$SkipPlaywright = $false,
    [switch]$NonInteractive = $false
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "   Thundr Daily Rewards Runner - Quick Setup Script   " -ForegroundColor Yellow
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify Python
Write-Host "[1/4] Checking Python installation..." -ForegroundColor Blue
try {
    $pyVersion = python --version 2>&1
    Write-Host "      Found: $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "      ERROR: Python is not installed or not in PATH." -ForegroundColor Red
    Write-Host "      Please install Python 3.10+ from python.org or Microsoft Store." -ForegroundColor Yellow
    Exit 1
}

# 2. Install dependencies
Write-Host "`n[2/4] Installing Python dependencies from requirements.txt..." -ForegroundColor Blue
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Write-Host "      Dependencies installed successfully." -ForegroundColor Green

# 3. Playwright browser setup
if (-not $SkipPlaywright) {
    Write-Host "`n[3/4] Installing Playwright Chromium browser..." -ForegroundColor Blue
    python -m playwright install chromium
    Write-Host "      Playwright Chromium ready." -ForegroundColor Green
} else {
    Write-Host "`n[3/4] Skipped Playwright installation." -ForegroundColor Yellow
}

# 4. Configuration check
Write-Host "`n[4/4] Checking configuration (.env / data/accounts.json)..." -ForegroundColor Blue
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "      Created .env from .env.example." -ForegroundColor Green
    }
}

if (-not $NonInteractive) {
    Write-Host ""
    Write-Host "------------------------------------------------------" -ForegroundColor Gray
    Write-Host "Interactive Configuration (Press Enter to skip/keep default)" -ForegroundColor Cyan
    Write-Host "------------------------------------------------------" -ForegroundColor Gray
    
    $tgToken = Read-Host "Telegram Bot Token (optional)"
    $tgChat = Read-Host "Telegram Chat ID (optional)"
    
    if ($tgToken -and $tgChat) {
        $envContent = Get-Content ".env" -Raw
        $envContent = $envContent -replace "TELEGRAM_BOT_TOKEN=.*", "TELEGRAM_BOT_TOKEN=$tgToken"
        $envContent = $envContent -replace "TELEGRAM_CHAT_ID=.*", "TELEGRAM_CHAT_ID=$tgChat"
        Set-Content ".env" $envContent
        Write-Host "Telegram settings saved to .env." -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  Setup completed successfully!                       " -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host "Quick commands:" -ForegroundColor Yellow
Write-Host "  python cli.py test                     - Run diagnostics"
Write-Host "  python cli.py add-account              - Add Thundr account"
Write-Host "  python cli.py check                    - Check accounts status"
Write-Host "  python cli.py run                      - Run daily claimer"
Write-Host ""
