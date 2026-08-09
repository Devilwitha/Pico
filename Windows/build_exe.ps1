<#
.SYNOPSIS
  Baut die Pico-Steuerung-Windows-App (Windows\PicoSteuerung) als
  eigenstaendige .exe.

.DESCRIPTION
  Erstellt per "dotnet publish" eine selbststaendige, einzelne .exe-Datei
  (enthaelt die .NET-Runtime, es muss also auf dem Zielrechner nichts
  vorinstalliert sein). Benoetigt das .NET SDK (8.0 oder neuer); fehlt es,
  versucht das Skript, es automatisch per winget zu installieren.

.PARAMETER Runtime
  Ziel-Runtime fuer den Build, z.B. win-x64 (Standard) oder win-arm64.

.EXAMPLE
  .\build_exe.ps1

.EXAMPLE
  .\build_exe.ps1 -Runtime win-arm64
#>

param(
    [string]$Runtime = 'win-x64'
)

$ErrorActionPreference = 'Stop'

$ProjektPfad = Join-Path $PSScriptRoot 'PicoSteuerung\PicoSteuerung.csproj'
$AusgabePfad = Join-Path $PSScriptRoot 'PicoSteuerung\publish'

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host '== Pico-Steuerung: Windows-App (.exe) bauen ==' -ForegroundColor Cyan

if (-not (Test-Path $ProjektPfad)) {
    Write-Error "Projekt nicht gefunden unter: $ProjektPfad"
}

# --- 1) .NET SDK sicherstellen ------------------------------------------------
if (-not (Test-CommandExists 'dotnet')) {
    Write-Host '.NET SDK wurde nicht gefunden.' -ForegroundColor Yellow
    if (Test-CommandExists 'winget') {
        Write-Host 'Installiere .NET SDK per winget...' -ForegroundColor Yellow
        winget install -e --id Microsoft.DotNet.SDK.8 --accept-package-agreements --accept-source-agreements
        Write-Host 'Bitte dieses Fenster/Terminal neu starten, damit "dotnet" im PATH verfuegbar ist, und das Skript danach erneut ausfuehren.' -ForegroundColor Yellow
        exit 1
    } else {
        Write-Error '.NET SDK wurde nicht gefunden und winget ist nicht verfuegbar. Bitte manuell installieren: https://dotnet.microsoft.com/download'
    }
}

Write-Host ('.NET SDK: ' + (dotnet --version)) -ForegroundColor Green

# --- 2) Alte Build-Ausgabe entfernen -------------------------------------------
if (Test-Path $AusgabePfad) {
    Remove-Item -Recurse -Force $AusgabePfad
}

# --- 3) Veroeffentlichen (selbststaendige Einzeldatei-.exe) --------------------
Write-Host "Baue .exe fuer $Runtime (kann beim ersten Mal einige Minuten dauern)..." -ForegroundColor Cyan
dotnet publish $ProjektPfad -c Release -r $Runtime --self-contained true `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
    -o $AusgabePfad

if ($LASTEXITCODE -ne 0) {
    Write-Error 'Build fehlgeschlagen - siehe Ausgabe oben.'
}

$exe = Get-ChildItem -Path $AusgabePfad -Filter '*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1

Write-Host ''
if ($exe) {
    Write-Host "Fertig! Die App liegt unter: $($exe.FullName)" -ForegroundColor Green
    Write-Host 'Die .exe ist eigenstaendig und kann direkt auf jeden Windows-Rechner kopiert und gestartet werden.' -ForegroundColor Green
} else {
    Write-Host 'Build abgeschlossen, aber keine .exe gefunden - bitte Ausgabe oben pruefen.' -ForegroundColor Yellow
}
