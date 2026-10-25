@echo off
title Thundr Quick Switcher (Web & Desktop)
cd /d "%~dp0"
echo Starting Thundr Quick Switcher on port 8888...
python app.py 8888
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start Thundr Quick Switcher.
    pause
)
