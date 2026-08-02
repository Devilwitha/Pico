<#
.SYNOPSIS
  Installiert die zuletzt gebaute Android-APK der Pico-Steuerung-App auf
  einem per USB angeschlossenen Android-Geraet.

.DESCRIPTION
  Sucht die neueste APK unter android_app\bin (Ergebnis von build_apk.ps1),
  findet adb (ueber PATH oder gaengige Android-SDK-Installationsorte) und
  installiert die APK per "adb install -r" auf dem angeschlossenen Geraet.
  Auf dem Geraet muss vorher USB-Debugging aktiviert und die Verbindung zu
  diesem PC bestaetigt worden sein (Meldung "USB-Debugging zulassen?").

.PARAMETER ApkPath
  Pfad zu einer bestimmten APK. Ohne Angabe wird automatisch die zuletzt
  geaenderte *.apk unter android_app\bin verwendet.

.PARAMETER DeviceId
  Geraete-ID (aus "adb devices"), falls mehrere Geraete/Emulatoren
  gleichzeitig angeschlossen sind. Bei genau einem angeschlossenen Geraet
  nicht noetig.

.EXAMPLE
  .\install_apk.ps1

.EXAMPLE
  .\install_apk.ps1 -DeviceId emulator-5554
#>

param(
    [string]$ApkPath,
    [string]$DeviceId
)

$ErrorActionPreference = 'Stop'

$AndroidAppDir = Join-Path $PSScriptRoot 'android_app'

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host '== Pico-Steuerung: APK auf Android-Geraet installieren ==' -ForegroundColor Cyan

# --- 1) adb finden -----------------------------------------------------------
$adb = $null
if (Test-CommandExists 'adb') {
    $adb = 'adb'
} else {
    $kandidaten = @(
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
        (if ($env:ANDROID_HOME) { Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe' }),
        (if ($env:ANDROID_SDK_ROOT) { Join-Path $env:ANDROID_SDK_ROOT 'platform-tools\adb.exe' })
    ) | Where-Object { $_ -and (Test-Path $_) }

    if ($kandidaten) {
        $adb = $kandidaten[0]
    } else {
        Write-Error 'adb wurde nicht gefunden. Bitte Android SDK Platform-Tools installieren (https://developer.android.com/tools/releases/platform-tools) und sicherstellen, dass adb im PATH liegt oder ANDROID_HOME/ANDROID_SDK_ROOT gesetzt ist.'
    }
}
Write-Host "Verwende adb: $adb" -ForegroundColor DarkGray

# --- 2) APK finden -------------------------------------------------------------
if (-not $ApkPath) {
    $apk = Get-ChildItem -Path (Join-Path $AndroidAppDir 'bin') -Filter '*.apk' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $apk) {
        Write-Error "Keine APK unter android_app\bin gefunden. Zuerst '.\build_apk.ps1' ausfuehren, oder -ApkPath angeben."
    }
    $ApkPath = $apk.FullName
}
if (-not (Test-Path $ApkPath)) {
    Write-Error "APK nicht gefunden unter: $ApkPath"
}
Write-Host "APK: $ApkPath" -ForegroundColor Green

# --- 3) Angeschlossene Geraete ermitteln ---------------------------------------
$geraeteZeilen = & $adb devices | Select-Object -Skip 1 | Where-Object { $_.Trim() -ne '' }
$geraete = $geraeteZeilen | ForEach-Object {
    $teile = $_ -split "\s+"
    [PSCustomObject]@{ Id = $teile[0]; Status = $teile[1] }
}

$bereit = $geraete | Where-Object { $_.Status -eq 'device' }
$nichtBereit = $geraete | Where-Object { $_.Status -ne 'device' }
if ($nichtBereit) {
    foreach ($g in $nichtBereit) {
        Write-Host "Geraet $($g.Id) ist nicht bereit (Status: $($g.Status)) - auf dem Geraet ggf. 'USB-Debugging zulassen?' bestaetigen." -ForegroundColor Yellow
    }
}

if (-not $bereit) {
    Write-Error 'Kein einsatzbereites Android-Geraet gefunden. Geraet per USB anschliessen, USB-Debugging in den Entwickleroptionen aktivieren und die Verbindung auf dem Geraet bestaetigen.'
}

if ($DeviceId) {
    if (-not ($bereit | Where-Object { $_.Id -eq $DeviceId })) {
        Write-Error "Geraet '$DeviceId' ist nicht in der Liste der einsatzbereiten Geraete. Gefunden: $($bereit.Id -join ', ')"
    }
    $zielId = $DeviceId
} elseif ($bereit.Count -gt 1) {
    Write-Error "Mehrere Geraete angeschlossen ($($bereit.Id -join ', ')) - bitte mit -DeviceId <id> das Zielgeraet angeben."
} else {
    $zielId = $bereit[0].Id
}
Write-Host "Zielgeraet: $zielId" -ForegroundColor Green

# --- 4) Installieren ------------------------------------------------------------
Write-Host 'Installiere APK (bestehende Installation wird ersetzt)...' -ForegroundColor Cyan
& $adb -s $zielId install -r $ApkPath

if ($LASTEXITCODE -ne 0) {
    Write-Error 'Installation fehlgeschlagen - siehe Ausgabe oben.'
}

Write-Host ''
Write-Host "Fertig! APK wurde auf $zielId installiert." -ForegroundColor Green
