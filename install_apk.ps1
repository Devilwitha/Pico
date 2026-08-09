<#
.SYNOPSIS
  Installiert die zuletzt gebaute Android-APK der Pico-Steuerung-App auf
  einem per USB angeschlossenen Android-Geraet.

.DESCRIPTION
  Sucht die neueste APK unter Picodesk\android_app\bin (Ergebnis von
  build_apk.ps1), findet adb (ueber PATH oder gaengige Android-SDK-
  Installationsorte) und installiert die APK per "adb install -r" auf dem
  angeschlossenen Geraet. Auf dem Geraet muss vorher USB-Debugging aktiviert
  und die Verbindung zu diesem PC bestaetigt worden sein (Meldung
  "USB-Debugging zulassen?").

.PARAMETER ApkPath
  Pfad zu einer bestimmten APK. Ohne Angabe wird automatisch die zuletzt
  geaenderte *.apk unter Picodesk\android_app\bin verwendet.

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

$AndroidAppDir = Join-Path $PSScriptRoot 'Picodesk\android_app'

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host '== Pico-Steuerung: APK auf Android-Geraet installieren ==' -ForegroundColor Cyan

# --- 1) adb finden (und bei Bedarf per winget installieren) -----------------
function Find-Adb {
    if (Test-CommandExists 'adb') { return 'adb' }
    $kandidaten = @((Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'))
    if ($env:ANDROID_HOME) { $kandidaten += Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe' }
    if ($env:ANDROID_SDK_ROOT) { $kandidaten += Join-Path $env:ANDROID_SDK_ROOT 'platform-tools\adb.exe' }
    $kandidaten = $kandidaten | Where-Object { Test-Path $_ }
    if ($kandidaten) { return $kandidaten[0] }

    # winget installiert Google.PlatformTools als "Portable"-Paket hierhin und
    # traegt den Ordner in den PATH ein - allerdings sieht die *aktuelle*
    # Sitzung diese PATH-Aenderung erst nach einem Neustart. Direkt nachsehen
    # erspart den Neustart in den meisten Faellen.
    $wingetTreffer = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') `
        -Filter 'adb.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if ($wingetTreffer) { return $wingetTreffer }

    return $null
}

$adb = Find-Adb
if (-not $adb) {
    Write-Host 'adb wurde nicht gefunden.' -ForegroundColor Yellow
    if (Test-CommandExists 'winget') {
        Write-Host 'Installiere Android SDK Platform-Tools per winget...' -ForegroundColor Yellow
        winget install -e --id Google.PlatformTools --accept-package-agreements --accept-source-agreements
        Write-Host ''

        # Nach der Installation direkt erneut suchen (findet das winget-
        # Portable-Paket meist auch ohne Neustart, siehe Find-Adb oben) -
        # nur falls das fehlschlaegt, ist ein Neustart noetig (z.B. wenn
        # winget stattdessen die "normalen" SDK Platform-Tools installiert hat).
        $adb = Find-Adb
        if (-not $adb) {
            Write-Host 'Platform-Tools wurden installiert, adb aber noch nicht auffindbar.' -ForegroundColor Yellow
            Write-Host 'Bitte dieses Fenster/Terminal (bzw. VS Code) neu starten, damit der PATH' -ForegroundColor Yellow
            Write-Host 'aktualisiert wird, und dieses Skript danach erneut ausfuehren.' -ForegroundColor Yellow
            exit 1
        }
        Write-Host 'adb gefunden.' -ForegroundColor Green
    } else {
        Write-Error 'adb wurde nicht gefunden und winget ist nicht verfuegbar. Bitte Android SDK Platform-Tools manuell installieren: https://developer.android.com/tools/releases/platform-tools'
    }
}
Write-Host "Verwende adb: $adb" -ForegroundColor DarkGray

# --- 2) APK finden -------------------------------------------------------------
if (-not $ApkPath) {
    $apk = Get-ChildItem -Path (Join-Path $AndroidAppDir 'bin') -Filter '*.apk' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $apk) {
        Write-Error "Keine APK unter Picodesk\android_app\bin gefunden. Zuerst '.\build_apk.ps1' ausfuehren, oder -ApkPath angeben."
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
