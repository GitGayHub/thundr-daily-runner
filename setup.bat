@echo off
chcp 65001 >nul
echo ======================================================
echo    Thundr Daily Rewards Runner - Quick Setup (CMD)   
echo ======================================================
echo.

echo [1/3] Checking Python installation...
python --version
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

echo.
echo [2/3] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo [3/3] Setting up Playwright Chromium...
python -m playwright install chromium

if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo Created .env from template.
    )
)

echo.
echo ======================================================
echo Setup completed!
echo ======================================================
echo Run: python cli.py test
echo Run: python cli.py add-account
echo Run: python cli.py run
pause
