# Projekte

Dieses Repository enthaelt mehrere unabhaengige Projekte:

| Ordner | Projekt |
|---|---|
| `Picodesk/` | Raspberry Pi Pico W Sitz-/Steh-Tisch-Steuerung (Firmware, Web-UI, Android-App, Windows-App) - siehe [Picodesk/README.md](Picodesk/README.md) |
| `pcmonitor/` | PC-Monitor (separates Projekt) |

## Build-/Install-Skripte

Die PowerShell-Skripte fuer `Picodesk/` liegen bewusst hier im Root, nicht im
Projektordner:

| Skript | Zweck |
|---|---|
| `build_apk.ps1` | Baut die Android-APK von `Picodesk/android_app/` (per Docker) |
| `install_apk.ps1` | Installiert die gebaute APK auf einem per USB angeschlossenen Android-Geraet |
| `build_exe.ps1` | Baut die Windows-App von `Picodesk/Windows/PicoSteuerung/` als eigenstaendige `.exe` |

Details und Voraussetzungen siehe die jeweiligen `.SYNOPSIS`/`.DESCRIPTION`
im Skriptkopf sowie [Picodesk/README.md](Picodesk/README.md).
