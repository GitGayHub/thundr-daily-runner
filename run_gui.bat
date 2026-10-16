@echo off
title Thundr Daily Rewards Manager - GUI
cd /d "%~dp0"
echo Starting Thundr Rewards GUI...
python gui.py
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start GUI. Make sure Python 3.12 and dependencies are installed.
    pause
)
