@echo off
title Image to 3D — GPU Launcher
echo ================================
echo  Image to 3D — GPU Launcher
echo ================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11 or 3.12 first.
    pause
    exit /b 1
)

:: Install dependencies if needed
if not exist ".deps_installed" (
    echo [SETUP] Installing dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo. > .deps_installed
    echo [SETUP] Dependencies installed.
    echo.
)

:: Start API
echo [INFO] Starting server...
echo [INFO] Open http://localhost:8080 in your browser
echo [INFO] Press CTRL+C to stop
echo.
python -m uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
pause
