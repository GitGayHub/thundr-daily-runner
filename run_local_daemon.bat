@echo off
title Thundr Autonomous Runner & Daemon
cd /d "%~dp0"
echo ===================================================
echo   ⚡ Thundr Autonomous Rewards Runner & Daemon ⚡
echo ===================================================
echo.
echo Running initial claim across all configured accounts...
python cli.py run
echo.
echo Starting autonomous background daemon and live Telegram bot listener...
echo Auto-claims every 5 hours + instant response to /goal, /boost, /status, /claim
echo Press Ctrl+C at any time to pause or exit.
echo.
python cli.py daemon --interval 5
if errorlevel 1 (
    echo.
    echo [ERROR] Runner stopped unexpectedly.
    pause
)
