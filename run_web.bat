@echo off
title Thundr Quick Switcher - Web App
cd /d "%~dp0"
set "PATH=C:\Program Files\GitHub CLI;C:\Users\SWIFT\AppData\Local\Programs\Python\Python312;C:\Users\SWIFT\AppData\Local\Programs\Python\Python312\Scripts;%PATH%"
echo Starting Thundr Quick Switcher Web App...
python app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start Web App. Make sure Python and dependencies are installed.
    pause
)
