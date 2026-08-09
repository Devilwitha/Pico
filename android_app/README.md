# Pico Steuerung - Android-App

Native Android-App (Python/[Kivy](https://kivy.org)), die dieselben Funktionen
wie die Pico-Weboberflaeche (`index.html`/`einstellungen.html`/`dateien.html`)
bietet: Steuerung (Auf/Ab/Stehen/Sitzen, Position setzen), Automatik,
Bewegungserkennung, Verlauf, Geraetename, WLAN-Einstellungen, Dateiverwaltung
und Update-Upload - nativ dargestellt statt im Browser.

## Aufbau

| Datei | Zweck |
|---|---|
| `main.py` | Kivy-App: UI (Steuerung-/Einstellungen-/Dateien-Bildschirm) und Ablaufsteuerung |
| `picoclient.py` | HTTP-Client fuer die REST-API des Pico (`/info`, `/aktion/...`, `/wlan/...`, `/dateien/...`, `/update`, `/name/...`) |
| `discovery.py` | UDP-Broadcast-Discovery, findet den Pico automatisch im Netz |
| `wifi_android.py` | Android-spezifisches automatisches Verbinden mit dem Pico-Hotspot |
| `icon.png` | App-Icon (identisch zum Icon der Windows-App) |
| `presplash.png` | Splash-Screen, kurz beim Start sichtbar (identisch zur Windows-App) |
| `buildozer.spec` | Build-Konfiguration fuer die APK |

## Funktionen im Detail

- **Geraetename**: Auf dem Einstellungen-Bildschirm frei vergebbar (Endpunkt
  `/name/speichern`) - hilfreich, um bei mehreren Picos im Netz eindeutig zu
  erkennen, mit welchem Geraet man verbunden ist. Wird zusaetzlich prominent
  ueber der Verbindungszeile auf dem Steuerung-Bildschirm angezeigt.
- **Position setzen**: Die beiden Buttons "Stehposition setzen"/
  "Sitzposition setzen" halten dasselbe Relais wie "Stehen"/"Sitzen" laenger
  gedrueckt, damit das Aktuator-Steuergeraet die aktuelle Position einlernt
  (siehe `IMPULS_DAUER_SETZEN` in `main.py` auf dem Pico).
- **Dateien** (Ordner-Symbol in der Kopfleiste): Dateien auf dem Pico
  durchsuchen, bearbeiten, neu anlegen und loeschen - identisch zu
  `dateien.html`. Speichern/Anlegen laeuft ueber denselben `/update`-
  Mechanismus wie unten beschrieben. `main.py`/`boot.py` sind vor dem
  Loeschen geschuetzt.
- **Update**: Karte unten auf dem Steuerung-Bildschirm - waehlt eine Datei
  vom Geraet (per [plyer](https://github.com/kivy/plyer) `filechooser`) und
  laedt sie unter ihrem eigenen Namen auf den Pico hoch (Backup als
  `<name>.bak`, `.py`-Dateien werden geprueft und starten den Pico neu).

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
- Der Datei-Auswahldialog fuer das Update (`plyer.filechooser`) nutzt unter
  Android den systemeigenen Storage-Access-Framework-Picker; das Verhalten
  (verfuegbare Speicherorte, ob der zurueckgegebene Pfad direkt lesbar ist)
  kann je nach Android-Version/Hersteller leicht variieren.
- Die App wurde per Desktop-Kivy (identische Version 2.3.0 wie in
  `buildozer.spec`) auf allen drei Bildschirmen inkl. Datei-Editor und
  Bestaetigungsdialog lauffaehig getestet (keine Exceptions, korrektes
  Rendering) - ein echter Android-Build/Deploy auf ein Geraet war in dieser
  Session nicht moeglich (keine Android-Build-Umgebung verfuegbar). Vor dem
  produktiven Einsatz einmal `buildozer android debug deploy run logcat`
  gegen ein Testgeraet laufen lassen, insbesondere fuer den neuen
  Datei-Upload (Berechtigungen/SAF) und den Presplash.
