@echo off
setlocal

set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PC Monitor.lnk"

if exist "%LINK%" (
    del "%LINK%"
    echo Autostart-Verknuepfung entfernt.
) else (
    echo Keine Autostart-Verknuepfung gefunden - nichts zu tun.
)

echo.
pause
