@echo off
setlocal
cd /d "%~dp0"

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK=%STARTUP%\PC Monitor.lnk"
set "VBS=%TEMP%\pcmonitor_shortcut_%RANDOM%.vbs"

echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS%"
echo sLinkFile = "%LINK%" >> "%VBS%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBS%"
echo oLink.TargetPath = "%~dp0start.bat" >> "%VBS%"
echo oLink.WorkingDirectory = "%~dp0" >> "%VBS%"
echo oLink.WindowStyle = 7 >> "%VBS%"
echo oLink.Description = "Startet den PC Monitor Sender automatisch" >> "%VBS%"
echo oLink.Save >> "%VBS%"

cscript //nologo "%VBS%"
del "%VBS%"

if exist "%LINK%" (
    echo.
    echo ============================================
    echo  Autostart eingerichtet.
    echo  Der PC Monitor startet ab jetzt bei jeder
    echo  Windows-Anmeldung automatisch ^(minimiert^).
    echo ============================================
) else (
    echo.
    echo [FEHLER] Verknuepfung konnte nicht erstellt werden.
)

echo.
pause
