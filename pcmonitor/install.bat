-++
@echo off
setlocal

echo ============================================
echo  PC Monitor - Installation der Abhaengigkeiten
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

echo Gefundene Python-Version:
python --version
echo.

echo Aktualisiere pip...
python -m pip install --upgrade pip
if %errorlevel% neq 0 (
    echo [FEHLER] pip konnte nicht aktualisiert werden.
    pause
    exit /b 1
)

echo.
echo Installiere benoetigte Pakete (psutil, GPUtil)...
python -m pip install psutil GPUtil
if %errorlevel% neq 0 (
    echo [FEHLER] Installation der Pakete fehlgeschlagen.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Installation abgeschlossen.
echo  Start der PC-App mit: python pc_monitor_sender.py
echo ============================================
echo.
pause
