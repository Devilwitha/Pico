<#
.SYNOPSIS
  Baut die Android-APK der Pico-Steuerung-App (Ordner android_app/) unter Windows.

.DESCRIPTION
  Buildozer (das Kivy-Android-Build-Tool) laeuft nur unter Linux/macOS. Dieses
  Skript nutzt deshalb das offizielle "kivy/buildozer"-Docker-Image, das
  Android SDK/NDK, python-for-android und alle noetigen Werkzeuge bereits
  enthaelt - unter Windows wird dafuer nur Docker Desktop benoetigt. Fehlt
  Docker, versucht das Skript, es automatisch per winget zu installieren.

.PARAMETER Clean
  Fuehrt vor dem Bauen "buildozer android clean" aus (z.B. nach Aenderungen
  an buildozer.spec, die sonst nicht uebernommen wuerden).

.EXAMPLE
  .\build_apk.ps1

.EXAMPLE
  .\build_apk.ps1 -Clean
#>

param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$AndroidAppDir = Join-Path $PSScriptRoot 'android_app'
$DockerImage = 'kivy/buildozer'

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host '== Pico-Steuerung: Android-APK bauen ==' -ForegroundColor Cyan

if (-not (Test-Path $AndroidAppDir)) {
    Write-Error "Ordner 'android_app' wurde nicht gefunden unter: $AndroidAppDir"
}

# --- 1) Docker sicherstellen -------------------------------------------------
if (-not (Test-CommandExists 'docker')) {
    Write-Host 'Docker wurde nicht gefunden.' -ForegroundColor Yellow
    if (Test-CommandExists 'winget') {
        Write-Host 'Installiere Docker Desktop per winget (kann einen Neustart erfordern)...' -ForegroundColor Yellow
        winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
        Write-Host ''
        Write-Host 'Docker Desktop wurde installiert.' -ForegroundColor Yellow
        Write-Host 'Bitte den PC bei Bedarf neu starten, Docker Desktop einmal manuell' -ForegroundColor Yellow
        Write-Host 'starten (WSL2-Setup beim ersten Start abschliessen) und dieses' -ForegroundColor Yellow
        Write-Host 'Skript danach erneut ausfuehren.' -ForegroundColor Yellow
        exit 1
    } else {
        Write-Error 'Docker wurde nicht gefunden und winget ist nicht verfuegbar. Bitte Docker Desktop manuell installieren: https://www.docker.com/products/docker-desktop/'
    }
}

# --- 2) Pruefen, ob der Docker-Daemon laeuft --------------------------------
docker info > $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Docker Desktop laeuft nicht - versuche es zu starten...' -ForegroundColor Yellow
    $dockerDesktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $dockerDesktopExe) {
        Start-Process $dockerDesktopExe
    }

    Write-Host 'Warte, bis Docker bereit ist (bis zu 2 Minuten)...' -ForegroundColor Yellow
    $bereit = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        docker info > $null
        if ($LASTEXITCODE -eq 0) { $bereit = $true; break }
    }
    if (-not $bereit) {
        Write-Error 'Docker Desktop ist nicht rechtzeitig gestartet. Bitte manuell starten und dieses Skript erneut ausfuehren.'
    }
}

Write-Host 'Docker ist bereit.' -ForegroundColor Green

# --- 3) buildozer-Image aktuell halten --------------------------------------
Write-Host "Aktualisiere Docker-Image '$DockerImage' (kann beim ersten Mal mehrere Minuten dauern)..." -ForegroundColor Cyan
docker pull $DockerImage
if ($LASTEXITCODE -ne 0) {
    Write-Error "Konnte '$DockerImage' nicht laden. Internetverbindung pruefen oder Image-Name unter https://buildozer.readthedocs.io/en/latest/installation.html#android-on-windows nachschlagen (falls sich der offizielle Image-Name geaendert hat)."
}

# --- 4) Optional: vorher aufraeumen -----------------------------------------

# Persistentes Docker-Volume fuer den globalen buildozer-Cache (SDK/NDK/ANT
# unter /home/user/.buildozer im Container). Ohne dieses Volume wird bei
# jedem Lauf alles neu heruntergeladen, da der Container mit --rm nach dem
# Build geloescht wird.
$BuildozerCacheVolume = 'pico-buildozer-cache'
docker volume create $BuildozerCacheVolume > $null

if ($Clean) {
    Write-Host 'Fuehre "buildozer android clean" aus...' -ForegroundColor Cyan
    docker run --rm -v "${AndroidAppDir}:/home/user/hostcwd" -v "${BuildozerCacheVolume}:/home/user/.buildozer" $DockerImage android clean
}

# --- 5) APK bauen ------------------------------------------------------------
Write-Host 'Baue Debug-APK (beim ersten Mal koennen 15-30 Minuten wegen SDK/NDK-Download vergehen)...' -ForegroundColor Cyan
docker run --rm -v "${AndroidAppDir}:/home/user/hostcwd" -v "${BuildozerCacheVolume}:/home/user/.buildozer" $DockerImage -v android debug

if ($LASTEXITCODE -ne 0) {
    Write-Error 'Build fehlgeschlagen - siehe Ausgabe oben.'
}

$apk = Get-ChildItem -Path (Join-Path $AndroidAppDir 'bin') -Filter '*.apk' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

Write-Host ''
if ($apk) {
    Write-Host "Fertig! APK liegt unter: $($apk.FullName)" -ForegroundColor Green
} else {
    Write-Host 'Build abgeschlossen, aber keine APK in android_app\bin gefunden - bitte Ausgabe oben pruefen.' -ForegroundColor Yellow
}
