# Pico Steuerung

Raspberry Pi Pico W als WLAN-Steuerung fuer einen Sitz-/Steh-Tisch (4
Relais-Aktionen: Auf / Ab / Stehen / Sitzen), mit Weboberflaeche,
automatischem Sitzen/Stehen-Wechsel, Bewegungserkennung per
Ultraschallsensor und einer passenden Android-App.

## Inhalt

| Pfad | Zweck |
|---|---|
| `main.py` | Firmware fuer den Pico W (Webserver, Automatik, Bewegungserkennung) |
| `pico_tools/wlan.py` | Wiederverwendbares Modul: WLAN-Verbindung mit Hotspot-Fallback |
| `pico_tools/update.py` | Wiederverwendbares Modul: Datei-Update per HTTP-Upload |
| `index.html` | Web-Oberflaeche (Steuerung, Automatik, Bewegungserkennung, Verlauf, Update) |
| `einstellungen.html` | WLAN-Einstellungen/Recovery-Seite |
| `dateien.html` | Dateiverwaltung: Dateien auf dem Pico suchen, bearbeiten, anlegen, loeschen |
| `android_app/` | Native Android-App (Python/Kivy), siehe `android_app/README.md` |
| `Windows/` | Native Windows-App (C#/WPF), siehe `Windows/README.md` |

Die drei Build-/Install-Skripte (`build_apk.ps1`, `install_apk.ps1`, `build_exe.ps1`)
liegen im Repository-Root (eine Ebene ueber diesem Ordner), nicht hier -
siehe die jeweiligen Abschnitte unten.

## Pico einrichten

1. `main.py`, `index.html`, `einstellungen.html`, `dateien.html` und den
   Ordner `pico_tools/` (mit `__init__.py`, `wlan.py`, `update.py`) auf den
   Pico W kopieren (z.B. mit Thonny - Ordner per Rechtsklick auf das
   Pico-Dateisystem anlegen und die drei Dateien hineinlegen).
2. Beim ersten Start `WLAN_SSID`/`WLAN_PASSWORT` in `main.py` setzen, oder
   spaeter bequem ueber die "Einstellungen"-Seite im Browser - das legt eine
   `wlan.conf` auf dem Pico an, die beim naechsten Start automatisch geladen
   wird.
3. Der Pico ist danach unter seiner IP oder `http://pult.<lokale-domain>`
   erreichbar (Hostname ueber `GERAETENAME` in `main.py` einstellbar).

### WLAN-Ausfallsicherung (Hotspot + Recovery)

Schlaegt die Verbindung zum konfigurierten WLAN nach 3 Versuchen fehl,
oeffnet der Pico automatisch einen eigenen Hotspot
(`PicoSteuerung-Setup` / `picosetup123`, siehe `AP_SSID`/`AP_PASSWORT` in
`main.py`) und zeigt dort die Recovery-Seite `einstellungen.html` an, auf
der neue WLAN-Zugangsdaten eingegeben werden koennen - der Pico startet
danach neu und versucht es erneut. Bleibt der Hotspot laenger als 10 Minuten
ohne neue Einrichtung aktiv, startet der Pico von selbst neu und versucht es
wieder mit dem WLAN (`HOTSPOT_TIMEOUT_SEK`).

### Discovery

Der Pico beantwortet UDP-Broadcast-Anfragen auf Port 4210 (`PICO_DISCOVER`)
mit seiner IP, seinem Modus (WLAN/Hotspot) und seiner Version - dadurch
findet die Android-App ihn automatisch, egal ob er im Heim-WLAN oder im
eigenen Recovery-Hotspot laeuft.

### Updates aus dem Browser

Auf der Steuerungsseite koennen eine oder mehrere beliebige Dateien auf
einmal ausgewaehlt werden - jede wird unter ihrem eigenen Namen gespeichert.
Existiert bereits eine gleichnamige Datei auf dem Pico, wird nur diese
ersetzt (die alte Version bleibt als `<name>.bak` erhalten); andernfalls wird
die Datei neu angelegt. `.py`-Dateien werden vor der Uebernahme per
`compile()` auf gueltiges Python geprueft und starten den Pico danach neu,
alle anderen Dateien (z.B. `.html`) werden sofort ohne Neustart uebernommen.
Bei mehreren ausgewaehlten Dateien werden diese nacheinander hochgeladen,
`.py`-Dateien zuletzt: die Seite wartet nach einem Neustart automatisch, bis
der Pico wieder erreichbar ist, bevor die naechste Datei folgt.

### Dateiverwaltung

Auf der Seite `dateien.html` (verlinkt im Footer neben "Einstellungen")
lassen sich alle auf dem Pico gespeicherten Dateien durchsuchen, bearbeiten,
neu anlegen und loeschen - sowie ueber "Hochladen" auch direkt eine oder
mehrere Dateien vom Rechner hochladen (identisches Verhalten wie auf der
Steuerungsseite, s.o.). Bearbeiten/Anlegen/Hochladen laeuft ueber denselben
`/update`-Mechanismus wie oben beschrieben (inkl. Backup und Python-Pruefung
bei `.py`-Dateien). `main.py` und `boot.py` lassen sich zum Schutz vor einem
versehentlich lahmgelegten Pico nicht ueber die Dateiverwaltung loeschen.

## pico_tools (in anderen Projekten wiederverwenden)

Die WLAN-Verbindung mit Hotspot-Fallback und die Update-per-Upload-Logik
stecken nicht in `main.py`, sondern in den beiden eigenstaendigen Modulen
`pico_tools/wlan.py` und `pico_tools/update.py` - beide kennen nur einen
Socket-Client sowie generische `header_wert`/`http_antwort`-Funktionen und
sind damit unabhaengig von diesem Projekt. Fuer ein neues Pico-Projekt
reicht es, den Ordner `pico_tools/` (samt `__init__.py`) zu kopieren:

```python
from pico_tools import wlan, update

wlan.AP_SSID = "MeinProjekt-Setup"
wlan.AP_PASSWORT = "setup1234"
wlan.LED = Pin("LED", Pin.OUT)   # optional

_netz, modus = wlan.verbinden(
    hostname="mein-geraet",
    standard_ssid="Heim-WLAN",
    standard_passwort="geheim123",
)
if modus == "hotspot":
    _thread.start_new_thread(wlan.hotspot_timeout_thread, ())
```

Im HTTP-Dispatcher genuegen dann pro Modul ein bis zwei Zeilen, siehe
`main.py` (`/wlan/status`, `/wlan/speichern`, `/update`) als Referenz-
Integration.

## Android-App

Die App unter `android_app/` bildet die Weboberflaeche nativ nach und findet
den Pico automatisch per Discovery. Details, Architektur und bekannte
Einschraenkungen: [android_app/README.md](android_app/README.md).

### APK bauen (Windows)

Vom Repository-Root aus (eine Ebene ueber `Picodesk/`):

```powershell
.\build_apk.ps1
```

Baut ueber das offizielle `kivy/buildozer`-Docker-Image (installiert Docker
Desktop bei Bedarf automatisch per winget). Ergebnis liegt danach unter
`Picodesk\android_app\bin\*.apk`.

Direkt auf ein per USB angeschlossenes Android-Geraet installieren:

```powershell
.\install_apk.ps1
```

## Windows-App

Die App unter `Windows/` bildet die Weboberflaeche nativ nach (C#/WPF) und
findet den Pico automatisch per Discovery. Details: [Windows/README.md](Windows/README.md).

Vom Repository-Root aus bauen:

```powershell
.\build_exe.ps1
```

Ergebnis liegt danach unter `Picodesk\Windows\PicoSteuerung\publish\PicoSteuerung.exe`.
