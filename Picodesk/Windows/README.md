# Pico-Steuerung (Windows-App)

Eigenstaendige Windows-Anwendung (C#/WPF, .NET 8), die dieselbe Steuerung wie
die Weboberflaeche (`index.html`, `einstellungen.html`, `dateien.html`) als
natives Programm bereitstellt - inklusive automatischer Suche nach dem Pico
im Netz.

## Funktionen

| Tab | Entspricht | Funktionen |
|---|---|---|
| Steuerung | `index.html` | Auf/Ab (Halten), Stehen/Sitzen (Impuls), Position setzen, Automatik, Bewegungserkennung, Verlauf, Update-Upload |
| Einstellungen | `einstellungen.html` | Geraetename anzeigen/aendern, WLAN-Zugangsdaten anzeigen/aendern |
| Dateien | `dateien.html` | Dateien auf dem Pico suchen, bearbeiten, anlegen, loeschen, hochladen |

Die App spricht dieselben HTTP-Endpunkte an wie die Weboberflaeche (siehe
`main.py`, Abschnitt `anfrage_bearbeiten()`) - es ist also keine Aenderung
am Pico noetig.

Sowohl im Update-Bereich (Steuerung) als auch im Dateien-Tab lassen sich per
"Dateien waehlen..."/"Hochladen" mehrere Dateien auf einmal auswaehlen - sie
werden nacheinander hochgeladen (`.py`-Dateien zuletzt, da sie den Pico neu
starten; die App wartet danach automatisch, bis er wieder erreichbar ist,
bevor die naechste Datei folgt).

### Geraetename

Der auf der Einstellungen-Seite vergebene Anzeigename (siehe `/name/speichern`
in `main.py`) wird prominent in der Verbindungsleiste sowie im Fenstertitel
angezeigt und in der Ergebnisliste von "Automatisch suchen" verwendet -
hilfreich, um bei mehreren Picos im Netz eindeutig zu erkennen, mit welchem
Geraet man gerade verbunden ist.

### Automatische Verbindung

Beim Start versucht die App automatisch, den zuletzt verbundenen Pico wieder
zu erreichen (gespeichert unter `%AppData%\PicoSteuerung\config.json`).
Ohne gespeicherten Host (oder ueber den Button "Automatisch suchen") schickt
sie denselben UDP-Broadcast auf Port 4210 (`PICO_DISCOVER`) wie die
Android-App und verbindet sich automatisch mit dem ersten antwortenden
Pico - funktioniert sowohl im normalen WLAN als auch im Recovery-Hotspot
des Picos. Alternativ laesst sich Host/IP auch manuell eingeben.

### Icon & Splash-Screen

Die `.exe` nutzt dasselbe Icon wie die Android-App (`android_app/icon.png`,
als Mehrgroessen-`.ico` unter `PicoSteuerung/Resources/icon.ico`) und zeigt
beim Start kurz denselben Splash-Screen wie die Android-App
(`PicoSteuerung/Resources/splash.png`).

## Bauen

Benoetigt das [.NET 8 SDK](https://dotnet.microsoft.com/download) (oder
neuer). Das Build-Skript liegt im Repository-Root (zwei Ebenen ueber diesem
Ordner):

```powershell
..\..\build_exe.ps1
```

Erstellt eine eigenstaendige `PicoSteuerung.exe` unter
`PicoSteuerung\publish\` (enthaelt die .NET-Runtime, laeuft ohne weitere
Installation auf jedem Windows-Rechner).

Zum Entwickeln/Debuggen alternativ direkt mit dem SDK:

```powershell
cd PicoSteuerung
dotnet run
```

## Projektstruktur

| Pfad | Zweck |
|---|---|
| `PicoSteuerung/MainWindow.xaml(.cs)` | Oberflaeche mit den drei Tabs |
| `PicoSteuerung/Services/PicoClient.cs` | HTTP-Client fuer die Pico-Endpunkte |
| `PicoSteuerung/Services/PicoDiscovery.cs` | UDP-Discovery (Broadcast auf Port 4210) |
| `PicoSteuerung/Services/Einstellungen.cs` | Speichert den zuletzt verbundenen Host |
| `PicoSteuerung/Models/PicoModels.cs` | JSON-Datenklassen (1:1 zu main.py) |
