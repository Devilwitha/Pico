<#
.SYNOPSIS
  Installiert/aktualisiert alles automatisch: Pico-Firmware + Home-Assistant-Paket.

.DESCRIPTION
  1. Pico (192.168.1.100): vergleicht jede Datei mit der Version auf dem Pico
     und laedt nur geaenderte Dateien ueber /update hoch (.py zuletzt, wartet
     nach jedem Neustart, bis der Pico wieder antwortet).
  2. Home Assistant (192.168.1.201): richtet beim ersten Lauf das offizielle
     Samba-Add-on ein, kopiert Picodesk/home_assistant/picodesk.yaml nach
     /config/packages/, aktiviert Packages in configuration.yaml, prueft die
     Konfiguration und startet HA nur neu, wenn sich etwas geaendert hat.

  Einfach nach jeder Aenderung erneut ausfuehren - unveraenderte Teile werden
  uebersprungen.

  Beim ersten Lauf wird ein HA-Langzeit-Token abgefragt (HA -> Profil ->
  Sicherheit -> Langlebige Zugriffstoken) und unter
  %APPDATA%\Picodesk\ha_token.txt gespeichert.

.EXAMPLE
  .\deploy.ps1              # alles
  .\deploy.ps1 -NurHA       # nur Home Assistant
  .\deploy.ps1 -NurPico     # nur Pico
#>
param(
    [string]$PicoIp = "192.168.1.100",
    [string]$HaUrl = "http://192.168.1.201",
    [string]$HaToken = $env:HA_TOKEN,
    [switch]$NurPico,
    [switch]$NurHA
)

$ErrorActionPreference = "Stop"
$Projekt = Join-Path $PSScriptRoot "Picodesk"
$EinstellungsOrdner = Join-Path $env:APPDATA "Picodesk"
New-Item -ItemType Directory -Force $EinstellungsOrdner | Out-Null

function Normalisieren([string]$text) { ($text -replace "`r`n", "`n").TrimEnd() }

# ---------------------------------------------------------------------------
# Pico
# ---------------------------------------------------------------------------
function Warte-AufPico([int]$sekunden = 90) {
    $ende = (Get-Date).AddSeconds($sekunden)
    Start-Sleep -Seconds 3
    while ((Get-Date) -lt $ende) {
        try { return Invoke-RestMethod "http://$PicoIp/info" -TimeoutSec 4 } catch { Start-Sleep -Seconds 2 }
    }
    throw "Pico antwortet nach $sekunden s nicht mehr - ggf. per USB/Thonny die .bak-Datei zurueckspielen."
}

function Update-Pico {
    Write-Host "== Pico ($PicoIp) ==" -ForegroundColor Cyan
    $info = Invoke-RestMethod "http://$PicoIp/info" -TimeoutSec 10
    Write-Host "Verbunden: $($info.name), Version $($info.version)"

    # Reihenfolge: erst Dateien ohne Neustart, dann Module, main.py zuletzt
    $dateien = @(
        "index.html", "einstellungen.html", "dateien.html",
        "pico_tools/__init__.py", "pico_tools/update.py", "pico_tools/wlan.py",
        "pico_tools/github_update.py", "boot.py",
        "main.py"
    )
    $geaendert = 0
    foreach ($ziel in $dateien) {
        $lokal = Join-Path $Projekt $ziel
        $inhalt = [IO.File]::ReadAllText($lokal)
        $aufPico = $null
        try {
            $aufPico = (Invoke-WebRequest "http://$PicoIp/dateien/lesen?name=$($ziel)" -TimeoutSec 20).Content
        } catch { }
        if ($null -ne $aufPico -and (Normalisieren $aufPico) -eq (Normalisieren $inhalt)) {
            Write-Host "  = $ziel (aktuell)"
            continue
        }
        Write-Host "  > $ziel wird hochgeladen..." -ForegroundColor Yellow
        $bytes = [Text.Encoding]::UTF8.GetBytes($inhalt)
        $antwort = Invoke-RestMethod -Method Post -Uri "http://$PicoIp/update?ziel=$($ziel)" `
            -Body $bytes -ContentType "application/octet-stream" -TimeoutSec 60
        if (-not $antwort.ok) { throw "Upload von $ziel fehlgeschlagen: $($antwort.fehler)" }
        $geaendert++
        if ($antwort.neustart) {
            Write-Host "    Pico startet neu, warte..."
            $info = Warte-AufPico
        }
    }
    Write-Host "Pico fertig ($geaendert Datei(en) aktualisiert, Version $($info.version))." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Home Assistant
# ---------------------------------------------------------------------------
function Hole-Token {
    $datei = Join-Path $EinstellungsOrdner "ha_token.txt"
    if ($script:HaToken) { return }
    if (Test-Path $datei) { $script:HaToken = (Get-Content $datei -Raw).Trim(); return }
    Write-Host "Home-Assistant-Token benoetigt: HA -> Profil (unten links) -> Sicherheit ->"
    Write-Host "'Langlebige Zugriffstoken' -> 'Token erstellen'."
    $script:HaToken = (Read-Host "Token einfuegen").Trim()
    Set-Content -Path $datei -Value $script:HaToken -NoNewline
}

function Ha-Rest([string]$methode, [string]$pfad, $body = $null) {
    $param = @{
        Method = $methode; Uri = "$HaUrl$pfad"; TimeoutSec = 120
        Headers = @{ Authorization = "Bearer $script:HaToken" }; ContentType = "application/json"
    }
    if ($null -ne $body) { $param.Body = ($body | ConvertTo-Json -Depth 10 -Compress) }
    Invoke-RestMethod @param
}

# Supervisor-API ueber die WebSocket-API von HA (braucht nur das Token)
function Ha-Supervisor([string]$methode, [string]$endpunkt, $daten = $null) {
    $wsUrl = ($HaUrl -replace "^http", "ws") + "/api/websocket"
    $ws = [System.Net.WebSockets.ClientWebSocket]::new()
    $ct = [Threading.CancellationToken]::None
    $ws.ConnectAsync([uri]$wsUrl, $ct).Wait()
    function Senden($obj) {
        $b = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Depth 10 -Compress))
        $ws.SendAsync([ArraySegment[byte]]::new($b), "Text", $true, $ct).Wait()
    }
    function Empfangen {
        $puffer = [byte[]]::new(65536); $ms = [IO.MemoryStream]::new()
        do {
            $r = $ws.ReceiveAsync([ArraySegment[byte]]::new($puffer), $ct).Result
            $ms.Write($puffer, 0, $r.Count)
        } while (-not $r.EndOfMessage)
        [Text.Encoding]::UTF8.GetString($ms.ToArray()) | ConvertFrom-Json
    }
    try {
        Empfangen | Out-Null  # auth_required
        Senden @{ type = "auth"; access_token = $script:HaToken }
        $auth = Empfangen
        if ($auth.type -ne "auth_ok") { throw "HA-Token ungueltig." }
        $nachricht = @{ id = 1; type = "supervisor/api"; endpoint = $endpunkt; method = $methode; timeout = 600 }
        if ($null -ne $daten) { $nachricht.data = $daten }
        Senden $nachricht
        do { $antwort = Empfangen } while ($antwort.id -ne 1)
        if (-not $antwort.success) { throw "Supervisor $endpunkt : $($antwort.error.message)" }
        return $antwort.result
    } finally {
        $ws.Dispose()
    }
}

function Verbinde-Samba {
    $share = "\\$(([uri]$HaUrl).Host)\config"
    $zugang = Join-Path $EinstellungsOrdner "samba.json"
    if (Test-Path $share -ErrorAction SilentlyContinue) { return $share }

    if (-not (Test-Path $zugang)) {
        Write-Host "Richte Samba-Add-on in Home Assistant ein (einmalig)..."
        $slug = "core_samba"
        $installiert = $true
        try { $info = Ha-Supervisor "get" "/addons/$slug/info" } catch { $installiert = $false }
        if (-not $installiert -or -not $info.version) {
            Write-Host "  Installiere Samba-Add-on (kann einige Minuten dauern)..."
            Ha-Supervisor "post" "/store/addons/$slug/install" | Out-Null
            $info = Ha-Supervisor "get" "/addons/$slug/info"
        }
        $passwort = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
        $optionen = @{}
        $info.options.PSObject.Properties | ForEach-Object { $optionen[$_.Name] = $_.Value }
        $optionen.username = "picodesk"
        $optionen.password = $passwort
        if ($optionen.ContainsKey("enabled_shares") -and $optionen.enabled_shares -notcontains "config") {
            $optionen.enabled_shares = @($optionen.enabled_shares) + "config"
        }
        Ha-Supervisor "post" "/addons/$slug/options" @{ options = $optionen } | Out-Null
        Ha-Supervisor "post" "/addons/$slug/options" @{ boot = "auto" } | Out-Null
        try { Ha-Supervisor "post" "/addons/$slug/restart" | Out-Null } catch { Ha-Supervisor "post" "/addons/$slug/start" | Out-Null }
        @{ user = "picodesk"; password = $passwort } | ConvertTo-Json | Set-Content $zugang
        Start-Sleep -Seconds 10
    }
    $z = Get-Content $zugang -Raw | ConvertFrom-Json
    cmd /c "net use $share /user:$($z.user) $($z.password) /persistent:no" | Out-Null
    if (-not (Test-Path $share)) { throw "Samba-Freigabe $share nicht erreichbar." }
    return $share
}

# Lokales Add-on "Picodesk": Weboberflaeche des Pico per Ingress in der
# HA-Seitenleiste ("Tisch") - funktioniert auch per https/Nabu Casa/App.
function Update-Addon([string]$share) {
    $slug = "local_picodesk"
    $quelle = Join-Path $Projekt "home_assistant\addon_picodesk"
    $ziel = Join-Path "\\$(([uri]$HaUrl).Host)\addons" "picodesk"
    if (-not (Test-Path (Split-Path $ziel))) { throw "Samba-Freigabe 'addons' nicht erreichbar." }
    New-Item -ItemType Directory -Force $ziel | Out-Null

    $dateienGeaendert = $false
    foreach ($datei in Get-ChildItem $quelle -File) {
        $zielDatei = Join-Path $ziel $datei.Name
        if ($datei.Extension -in ".png", ".jpg") {
            # Bilder (icon.png/logo.png) binaer vergleichen und kopieren
            $neuHash = (Get-FileHash $datei.FullName).Hash
            $altHash = if (Test-Path $zielDatei) { (Get-FileHash $zielDatei).Hash } else { "" }
            if ($neuHash -ne $altHash) {
                Copy-Item $datei.FullName $zielDatei -Force
                $dateienGeaendert = $true
            }
            continue
        }
        $neu = (Get-Content $datei.FullName -Raw) -replace "`r`n", "`n"
        $alt = if (Test-Path $zielDatei) { (Get-Content $zielDatei -Raw) -replace "`r`n", "`n" } else { "" }
        if ($neu -ne $alt) {
            [IO.File]::WriteAllText($zielDatei, $neu)
            $dateienGeaendert = $true
        }
    }

    $installiert = $false
    try { $info = Ha-Supervisor "get" "/addons/$slug/info"; $installiert = [bool]$info.version } catch { }

    if (-not $installiert) {
        Write-Host "  > Add-on 'Picodesk' wird installiert (baut lokal, dauert ein paar Minuten)..." -ForegroundColor Yellow
        Ha-Supervisor "post" "/store/reload" | Out-Null
        Ha-Supervisor "post" "/store/addons/$slug/install" | Out-Null
    } elseif ($dateienGeaendert) {
        Write-Host "  > Add-on 'Picodesk' wird neu gebaut..." -ForegroundColor Yellow
        Ha-Supervisor "post" "/store/reload" | Out-Null
        $info = Ha-Supervisor "get" "/addons/$slug/info"
        if ($info.update_available) { Ha-Supervisor "post" "/store/addons/$slug/update" | Out-Null }
        else { Ha-Supervisor "post" "/addons/$slug/rebuild" | Out-Null }
    } else {
        Write-Host "  = Add-on 'Picodesk' (aktuell)"
    }

    Ha-Supervisor "post" "/addons/$slug/options" @{ boot = "auto"; ingress_panel = $true; watchdog = $true } | Out-Null
    $info = Ha-Supervisor "get" "/addons/$slug/info"
    if ($info.state -ne "started") { Ha-Supervisor "post" "/addons/$slug/start" | Out-Null }
    Write-Host "  Add-on laeuft - Seitenleiste -> 'Tisch'." -ForegroundColor Green
}

function Update-HA {
    Write-Host "== Home Assistant ($HaUrl) ==" -ForegroundColor Cyan
    Hole-Token
    $cfg = Ha-Rest "GET" "/api/config"
    Write-Host "Verbunden: HA $($cfg.version)"

    $share = Verbinde-Samba
    $geaendert = $false

    # Paket kopieren
    $quelle = Join-Path $Projekt "home_assistant\picodesk.yaml"
    $paketOrdner = Join-Path $share "packages"
    $ziel = Join-Path $paketOrdner "picodesk.yaml"
    New-Item -ItemType Directory -Force $paketOrdner | Out-Null
    $neu = Get-Content $quelle -Raw
    $alt = if (Test-Path $ziel) { Get-Content $ziel -Raw } else { "" }
    if ((Normalisieren $neu) -ne (Normalisieren $alt)) {
        [IO.File]::WriteAllText($ziel, ($neu -replace "`r`n", "`n"))
        Write-Host "  > packages/picodesk.yaml aktualisiert" -ForegroundColor Yellow
        $geaendert = $true
    } else { Write-Host "  = packages/picodesk.yaml (aktuell)" }

    # Packages in configuration.yaml aktivieren
    $configDatei = Join-Path $share "configuration.yaml"
    $config = Get-Content $configDatei -Raw
    if ($config -notmatch "(?m)^\s+packages:") {
        Copy-Item $configDatei "$configDatei.picodesk.bak" -Force
        $zeile = "  packages: !include_dir_named packages"
        if ($config -match "(?m)^homeassistant:\s*$") {
            $config = $config -replace "(?m)^homeassistant:\s*$", "homeassistant:`n$zeile"
        } else {
            $config = $config.TrimEnd() + "`n`nhomeassistant:`n$zeile`n"
        }
        [IO.File]::WriteAllText($configDatei, ($config -replace "`r`n", "`n"))
        Write-Host "  > configuration.yaml: Packages aktiviert (Backup: configuration.yaml.picodesk.bak)" -ForegroundColor Yellow
        $geaendert = $true
    } else { Write-Host "  = configuration.yaml (Packages bereits aktiv)" }

    Update-Addon $share

    if (-not $geaendert) { Write-Host "Home Assistant ist aktuell." -ForegroundColor Green; return }

    Write-Host "  Pruefe Konfiguration..."
    $pruefung = Ha-Rest "POST" "/api/config/core/check_config"
    if ($pruefung.result -ne "valid") {
        throw "HA-Konfiguration ungueltig, kein Neustart:`n$($pruefung.errors)"
    }
    Write-Host "  Starte Home Assistant neu..."
    try { Ha-Rest "POST" "/api/services/homeassistant/restart" | Out-Null } catch { }
    Write-Host "Home Assistant fertig - nach dem Neustart gibt es die Entitaeten 'Picodesk ...'." -ForegroundColor Green
}

if (-not $NurHA) { Update-Pico }
if (-not $NurPico) { Update-HA }
