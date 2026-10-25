@echo off
title Thundr Quick Switcher (Web & Desktop)
cd /d "%~dp0"
set "PATH=C:\Program Files\GitHub CLI;C:\Users\SWIFT\AppData\Local\Programs\Python\Python312;C:\Users\SWIFT\AppData\Local\Programs\Python\Python312\Scripts;%PATH%"
echo Starting Thundr Quick Switcher on port 8888...
python app.py 8888
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start Thundr Quick Switcher.
    pause
)
