@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  PC Monitor wird gestartet...
echo ============================================
echo.

python pc_monitor_sender.py
if %errorlevel% neq 0 (
    echo.
    echo [FEHLER] PC Monitor wurde mit einem Fehler beendet.
    echo          Falls "psutil"/"GPUtil" fehlen: install.bat ausfuehren.
    pause
    exit /b 1
)

pause
