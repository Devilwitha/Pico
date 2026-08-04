@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  LilyGO T-Display-S3 - Firmware flashen
echo ============================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [FEHLER] Python wurde nicht gefunden.
    echo          Bitte Python von https://www.python.org/downloads/ installieren
    echo          und beim Setup "Add python.exe to PATH" aktivieren.
    echo.
    pause
    exit /b 1
)

python flash_firmware.py
if %errorlevel% neq 0 (
    echo.
    echo [FEHLER] Firmware-Flash wurde mit einem Fehler beendet.
    pause
    exit /b 1
)

pause
