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
set HOST=%IMAGE3D_HOST%
if "%HOST%"=="" set HOST=127.0.0.1
if "%IMAGE3D_LAN%"=="1" set HOST=0.0.0.0
set PORT=%IMAGE3D_PORT%
if "%PORT%"=="" set PORT=8080
echo [INFO] Starting server...
echo [INFO] Binding to %HOST%:%PORT%
echo [INFO] Open http://localhost:%PORT% in your browser
if "%HOST%"=="0.0.0.0" echo [WARN] LAN exposure is enabled. Upload and generation routes are unauthenticated.
echo [INFO] Press CTRL+C to stop
echo.
python -m uvicorn api.main:app --host %HOST% --port %PORT% --reload
pause
