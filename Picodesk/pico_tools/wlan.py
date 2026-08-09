"""
pico_tools/wlan.py - WLAN-Verbindung mit Hotspot-Fallback (wiederverwendbar)

Verbindet sich mit einem WLAN, dessen Zugangsdaten in einer kleinen
JSON-Datei (Standard: "wlan.conf") gespeichert sind. Schlaegt die
Verbindung nach mehreren Versuchen fehl, oeffnet dieses Modul stattdessen
einen eigenen Access Point (Hotspot), ueber den mit einer Recovery-
Webseite (z.B. einstellungen.html) neue Zugangsdaten eingegeben werden
koennen - anschliessend startet das Geraet neu und versucht es erneut.

Enthaelt keinen eigenen Webserver, sondern nur Hilfsfunktionen fuer den
Dispatcher des aufrufenden main.py (siehe anfrage_bearbeiten weiter unten
sowie status()).

Verwendung in main.py:

    from pico_tools import wlan

    wlan.AP_SSID = "MeinProjekt-Setup"
    wlan.AP_PASSWORT = "setup1234"
    wlan.LED = Pin("LED", Pin.OUT)          # optional, blinkt beim Verbinden

    netz, modus = wlan.verbinden(
        hostname="mein-geraet",
        standard_ssid="Heim-WLAN",
        standard_passwort="geheim123",
    )
    if modus == "hotspot":
        _thread.start_new_thread(wlan.hotspot_timeout_thread, ())

Im Webserver-Dispatcher:

    elif pfad.startswith("/wlan/status"):
        http_antwort(client, "200 OK", json.dumps(wlan.status()), "application/json")
    elif methode == "POST" and pfad.startswith("/wlan/speichern"):
        wlan.speichern_anfrage_bearbeiten(
            client, lambda name: header_wert(kopf_text, name), body_bereits_gelesen, http_antwort
        )

Ueber wlan.modus ("normal"/"hotspot") und wlan.ip laesst sich der aktuelle
Zustand jederzeit abfragen, z.B. um zu entscheiden, welche Seite auf "/"
ausgeliefert wird.
"""

import network
import time
import json
import machine

# ---------------------------------------------------------------------------
# Konfiguration (vor dem Aufruf von verbinden() bei Bedarf ueberschreiben)
# ---------------------------------------------------------------------------

KONFIG_DATEI = "wlan.conf"

AP_SSID = "Pico-Setup"
AP_PASSWORT = "picosetup123"  # mind. 8 Zeichen (WPA2-Vorgabe)
HOTSPOT_TIMEOUT_SEK = 10 * 60

MAX_VERSUCHE = 3
VERSUCH_TIMEOUT = 15

# Optional: machine.Pin, blinkt waehrend des Verbindungsaufbaus und leuchtet
# dauerhaft, sobald die Verbindung steht
LED = None

# ---------------------------------------------------------------------------
# Status - von aussen lesbar, wird von verbinden()/hotspot_starten() gepflegt
# ---------------------------------------------------------------------------

modus = "normal"  # "normal" (im konfigurierten WLAN) oder "hotspot"
ip = ""

_hostname = ""
_hotspot_start_zeit = 0
_standard_ssid = ""
_standard_passwort = ""


def lade_zugangsdaten(standard_ssid="", standard_passwort=""):
    """Liest ssid/password aus KONFIG_DATEI, falls vorhanden, sonst die
    uebergebenen Standardwerte."""
    try:
        with open(KONFIG_DATEI) as f:
            daten = json.load(f)
            ssid = daten.get("ssid") or standard_ssid
            passwort = daten.get("password") or standard_passwort
            return ssid, passwort
    except OSError:
        return standard_ssid, standard_passwort


def speichern(ssid, passwort):
    """Schreibt neue WLAN-Zugangsdaten nach KONFIG_DATEI, von wo sie beim
    naechsten Aufruf von verbinden() geladen werden."""
    with open(KONFIG_DATEI, "w") as f:
        json.dump({"ssid": ssid, "password": passwort}, f)


def hostname_setzen(name):
    try:
        network.hostname(name)
    except (AttributeError, OSError):
        pass


def _mit_wlan_verbinden(ssid, passwort, hostname, timeout):
    global ip
    if hostname:
        hostname_setzen(hostname)

    sta = network.WLAN(network.STA_IF)
    if hostname:
        try:
            sta.config(hostname=hostname)
        except (ValueError, OSError):
            pass
    sta.active(True)
    sta.connect(ssid, passwort)

    start = time.time()
    while not sta.isconnected():
        if time.time() - start > timeout:
            raise RuntimeError("WLAN-Verbindung fehlgeschlagen (Timeout)")
        if LED is not None:
            LED.toggle()
        time.sleep(0.3)

    if LED is not None:
        LED.value(1)
    ip = sta.ifconfig()[0]
    print("Mit WLAN verbunden, IP-Adresse:", ip, "- Hostname:", hostname)
    return sta


def _verbinden_mit_wiederholung(ssid, passwort, hostname, versuche, timeout):
    for versuch in range(1, versuche + 1):
        try:
            return _mit_wlan_verbinden(ssid, passwort, hostname, timeout)
        except RuntimeError as exc:
            print("WLAN-Verbindungsversuch", versuch, "von", versuche, "fehlgeschlagen:", exc)
    return None


def hotspot_starten():
    """Oeffnet einen eigenen Access Point mit AP_SSID/AP_PASSWORT, ueber den
    eine Recovery-Seite erreichbar ist, um neue WLAN-Zugangsdaten
    einzugeben. Wird von verbinden() aufgerufen, wenn die Verbindung zum
    konfigurierten WLAN wiederholt fehlschlaegt."""
    global ip, modus, _hotspot_start_zeit
    modus = "hotspot"
    _hotspot_start_zeit = time.time()

    try:
        network.WLAN(network.STA_IF).active(False)
    except OSError:
        pass

    ap = network.WLAN(network.AP_IF)
    ap.config(ssid=AP_SSID, password=AP_PASSWORT)
    ap.active(True)
    while not ap.active():
        time.sleep(0.2)

    ip = ap.ifconfig()[0]
    print("Kein WLAN verbunden - Hotspot aktiv:", AP_SSID, "- IP:", ip)
    return ap


def verbinden(hostname="", standard_ssid="", standard_passwort="", versuche=None, timeout=None):
    """Laedt die Zugangsdaten (KONFIG_DATEI, sonst standard_ssid/-passwort),
    versucht mehrfach die Verbindung zum WLAN und oeffnet bei Misserfolg
    (oder falls keine SSID bekannt ist) stattdessen den Recovery-Hotspot.
    Gibt (netzwerk_objekt, modus) zurueck, modus ist "normal" oder
    "hotspot"."""
    global modus, _hostname, _standard_ssid, _standard_passwort
    _hostname = hostname
    _standard_ssid = standard_ssid
    _standard_passwort = standard_passwort
    versuche = MAX_VERSUCHE if versuche is None else versuche
    timeout = VERSUCH_TIMEOUT if timeout is None else timeout

    ssid, passwort = lade_zugangsdaten(standard_ssid, standard_passwort)
    netz = _verbinden_mit_wiederholung(ssid, passwort, hostname, versuche, timeout) if ssid else None

    if netz is not None:
        modus = "normal"
        return netz, modus

    return hotspot_starten(), "hotspot"


def status():
    """Fuer einen /wlan/status-Endpunkt: aktueller Modus, IP, Hostname und
    (im Hotspot-Modus) Hotspot-SSID sowie Restzeit bis zum automatischen
    Neustart."""
    konfigurierte_ssid, _ = lade_zugangsdaten(_standard_ssid, _standard_passwort)
    daten = {
        "modus": modus,
        "ip": ip,
        "hostname": _hostname,
        "ssid_konfiguriert": konfigurierte_ssid,
    }
    if modus == "hotspot":
        daten["hotspot_ssid"] = AP_SSID
        rest = HOTSPOT_TIMEOUT_SEK - (time.time() - _hotspot_start_zeit)
        daten["hotspot_rest_sek"] = max(0, int(rest))
    return daten


def speichern_anfrage_bearbeiten(client, header_wert, body_bereits_gelesen, http_antwort, max_bytes=1024):
    """Behandelt eine komplette POST-Anfrage zum Speichern neuer WLAN-
    Zugangsdaten (z.B. fuer /wlan/speichern): liest {"ssid", "password"}
    aus dem Body, speichert sie per speichern() und startet das Geraet neu,
    damit main() beim naechsten Boot verbinden() erneut mit den neuen
    Zugangsdaten aufruft.

    header_wert: Funktion, die einen Header-Namen entgegennimmt und dessen
    Wert (oder None) liefert, z.B. lambda name: header_wert(kopf_text, name).
    http_antwort: Funktion http_antwort(client, status, inhalt, content_type).
    """
    laenge_text = header_wert("Content-Length")
    laenge = int(laenge_text) if laenge_text and laenge_text.isdigit() else 0

    if laenge <= 0 or laenge > max_bytes:
        http_antwort(
            client, "400 Bad Request",
            json.dumps({"ok": False, "fehler": "Ungueltige Anfrage"}),
            "application/json",
        )
        return

    daten_roh = body_bereits_gelesen
    while len(daten_roh) < laenge:
        stueck = client.recv(min(1024, laenge - len(daten_roh)))
        if not stueck:
            break
        daten_roh += stueck

    try:
        daten = json.loads(daten_roh)
        ssid = (daten.get("ssid") or "").strip()
        passwort = daten.get("password") or ""
        if not ssid:
            raise ValueError("SSID darf nicht leer sein")
        speichern(ssid, passwort)
        http_antwort(client, "200 OK", json.dumps({"ok": True}), "application/json")
        client.close()
        time.sleep(0.5)
        machine.reset()
    except (ValueError, OSError) as exc:
        http_antwort(
            client, "400 Bad Request",
            json.dumps({"ok": False, "fehler": str(exc)}),
            "application/json",
        )


def hotspot_timeout_thread(tick=None, timeout_sek=None):
    """Fuer den Hotspot-Modus in einem eigenen Thread starten: laeuft in
    einer Sekunden-Schleife (ruft dabei optional einmal pro Sekunde tick()
    auf, z.B. fuer eine Discovery-Antwort) und startet das Geraet nach
    timeout_sek (Standard: HOTSPOT_TIMEOUT_SEK) automatisch neu, damit
    regelmaessig erneut versucht wird, sich mit dem konfigurierten WLAN zu
    verbinden."""
    warte_sek = HOTSPOT_TIMEOUT_SEK if timeout_sek is None else timeout_sek
    start = time.time()
    while time.time() - start < warte_sek:
        if tick is not None:
            tick()
        time.sleep(1)
    print(int(warte_sek), "Sek. im Hotspot ohne Einrichtung - versuche erneut das WLAN")
    machine.reset()
