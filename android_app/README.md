# Pico Steuerung - Android-App

Native Android-App (Python/[Kivy](https://kivy.org)), die dieselben Funktionen
wie die Pico-Weboberflaeche (`index.html`/`einstellungen.html`) bietet:
Steuerung (Auf/Ab/Stehen/Sitzen), Automatik, Bewegungserkennung, Verlauf und
WLAN-Einstellungen - nativ dargestellt statt im Browser.

## Aufbau

| Datei | Zweck |
|---|---|
| `main.py` | Kivy-App: UI (Steuerung-/Einstellungen-Bildschirm) und Ablaufsteuerung |
| `picoclient.py` | HTTP-Client fuer die REST-API des Pico (`/info`, `/aktion/...`, `/wlan/...`, ...) |
| `discovery.py` | UDP-Broadcast-Discovery, findet den Pico automatisch im Netz |
| `wifi_android.py` | Android-spezifisches automatisches Verbinden mit dem Pico-Hotspot |
| `buildozer.spec` | Build-Konfiguration fuer die APK |

## Automatisches Finden des Pico

Die App schickt beim Start (und immer wenn keine Verbindung besteht) ein
UDP-Broadcast-Paket (`PICO_DISCOVER` an Port 4210). Der Pico beantwortet das
in `main.py` (`discovery_tick()`) mit seiner IP-Adresse - das funktioniert
sowohl im normalen WLAN als auch im Pico-eigenen Recovery-Hotspot, da beides
nur ein IP-Subnetz ist. Die zuletzt gefundene IP wird lokal gespeichert
(`JsonStore`), sodass die App beim naechsten Start meist ohne erneute Suche
sofort verbindet.

## Verbindung zum Pico-Hotspot (Android)

Schlaegt die WLAN-Verbindung des Pico fehl, oeffnet er einen eigenen Hotspot
(siehe `AP_SSID`/`AP_PASSWORT` in `main.py` auf dem Pico). Seit Android 10
duerfen Apps sich nicht mehr unsichtbar automatisch mit einem WLAN
verbinden - der naechstliegende Weg ist eine **WifiNetworkSuggestion**
(`wifi_android.hotspot_vorschlagen()`): Android verbindet sich damit von
selbst, sobald der Hotspot in Reichweite ist, nachdem der Nutzer App-
Netzwerkvorschlaege einmalig unter *Einstellungen -> WLAN ->
Netzwerkvorschlaege* erlaubt hat. Als garantiert funktionierender Fallback
oeffnet "WLAN-Einstellungen" direkt die System-WLAN-Liste zum manuellen
Verbinden.

Der Pico selbst beendet seinen Hotspot automatisch nach 10 Minuten und
versucht danach wieder, sich mit dem konfigurierten WLAN zu verbinden
(`HOTSPOT_TIMEOUT_SEK` in `main.py`).

## Build (Windows)

Buildozer (das Kivy-Android-Build-Tool) laeuft nur unter Linux. Unter
Windows daher `build_apk.ps1` im Projekt-Root ausfuehren - das Skript
nutzt automatisch das offizielle `kivy/buildozer`-Docker-Image (installiert
bei Bedarf Docker Desktop per winget):

```powershell
.\build_apk.ps1
```

Die fertige APK liegt danach unter `android_app\bin\*.apk`.

## Build (Linux/macOS, direkt mit Buildozer)

```bash
pip install buildozer
cd android_app
buildozer android debug
```

## Bekannte Einschraenkungen

- Die automatische Hotspot-Verbindung (`WifiNetworkSuggestion`) ist von
  Android-Version und Geraetehersteller abhaengig und wurde nicht auf allen
  Geraeten getestet - der "WLAN-Einstellungen"-Button ist der zuverlaessige
  manuelle Fallback.
- Die App wurde nicht on-device gebaut/getestet (keine Android-Build-Umgebung
  in dieser Session verfuegbar) - vor dem produktiven Einsatz einmal
  `buildozer android debug deploy run logcat` gegen ein Testgeraet laufen
  lassen.
