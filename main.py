"""
Pico W Steuerung - WLAN Webserver mit 4 Tasten (Auf / Ab / Stehen / Sitzen)

Verbindet sich mit einem bestehenden WLAN und startet einen Webserver mit
moderner Oberflaeche. Ueber vier Buttons koennen GPIO-Ausgaenge angesteuert
werden (z.B. fuer Relais, Motorsteuerung o.ae.). Enthaelt ausserdem eine
Automatik (zeitgesteuerter Wechsel Sitzen/Stehen), eine Bewegungserkennung
per Grove Ultrasonic Ranger (aktiviert die Automatik automatisch, sobald sich
die gemessene Distanz um mehr als einen im Web einstellbaren Schwellwert
aendert - "kein Echo" zaehlt dabei nicht als Aenderung) und eine
WLAN-Update-Funktion: im Browser eine neue Version dieser Datei hochladen,
der Pico prueft sie auf gueltiges Python, ersetzt main.py und startet neu.

Die WLAN-Verbindung (inkl. Hotspot-Fallback) und die Update-Funktion stecken
nicht hier, sondern in den wiederverwendbaren Modulen pico_tools/wlan.py und
pico_tools/update.py (koennen 1:1 in andere Pico-Projekte kopiert werden).

WLAN-Einstellungen werden in "wlan.conf" gespeichert und beim Start geladen.
Schlaegt die Verbindung nach mehreren Versuchen fehl, oeffnet der Pico einen
eigenen Hotspot mit einer Recovery-Seite (einstellungen.html), auf der neue
Zugangsdaten eingegeben werden koennen - danach startet der Pico neu und
versucht es erneut. Bleibt der Hotspot laenger als HOTSPOT_TIMEOUT_SEK ohne
neue Einrichtung aktiv, startet der Pico von selbst neu und versucht es
wieder mit dem konfigurierten WLAN. Ausserdem antwortet der Pico auf
UDP-Broadcast-Anfragen (DISCOVERY_PORT), damit ihn Clients (z.B. die
Android-App) automatisch im Netz bzw. im eigenen Hotspot finden.

Einfach WLAN_SSID / WLAN_PASSWORT unten eintragen (oder ueber die
Einstellungen-Seite im Browser setzen, das erzeugt automatisch eine Datei
"wlan.conf" mit {"ssid": "...", "password": "..."} neben diesem Skript) und
als main.py auf den Pico W kopieren.

Verkabelung Grove Ultrasonic Ranger (siehe PIN_SIG_ULTRASCHALL unten):
  Schwarz (GND) -> GND
  Rot     (VCC) -> 3V3 OUT (nicht an 5V/VBUS, GPIOs sind nicht 5V-tolerant!)
  Gelb    (SIG) -> GPIO aus PIN_SIG_ULTRASCHALL
  Weiss   (NC)  -> nicht anschliessen
"""

import socket
import time
import json
import gc
import os
import machine
import _thread
from machine import Pin, time_pulse_us

from pico_tools import wlan, update

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

VERSION = "1.2.0"

WLAN_SSID = "FRITZ!Box 5530 BA_2GEXT"
WLAN_PASSWORT = "1234567890"

# Maximale Groesse einer Update-Datei in Bytes (Sicherheitsgrenze)
UPDATE_MAX_BYTES = 400_000

# Geraetename, unter dem der Pico im lokalen Netz per DHCP angemeldet wird.
# Der Router haengt die lokale Domain an (z.B. "tisch"), damit ist das
# Geraet dann als "pult.tisch" erreichbar statt nur ueber die IP-Adresse.
GERAETENAME = "pult"

# GPIO-Pins fuer die vier Aktionen (an eigene Verkabelung anpassen)
PIN_AUF = 13
PIN_AB = 10
PIN_STEHEN = 11
PIN_SITZEN = 12

# GPIO fuer die SIG-Leitung (gelbes Kabel) des Grove Ultrasonic Ranger
PIN_SIG_ULTRASCHALL = 15

# Wie oft und wie lange die Verbindung zum konfigurierten WLAN versucht wird,
# bevor der Pico stattdessen den Recovery-Hotspot oeffnet
WLAN_MAX_VERSUCHE = 3
WLAN_VERSUCH_TIMEOUT = 15

# Zugangsdaten und Verhalten des Recovery-Hotspots (aktiv, wenn keine
# Verbindung zum konfigurierten WLAN moeglich ist)
AP_SSID = "PicoSteuerung-Setup"
AP_PASSWORT = "picosetup123"  # mind. 8 Zeichen (WPA2-Vorgabe)
HOTSPOT_TIMEOUT_SEK = 10 * 60

# Port fuer die UDP-Discovery, ueber die Clients (z.B. die App) den Pico per
# Broadcast im WLAN oder im Hotspot automatisch finden
DISCOVERY_PORT = 4210
DISCOVERY_ANFRAGE = b"PICO_DISCOVER"

# Wie lange "stehen"/"sitzen" aktiviert werden (Sekunden). "auf"/"ab" sind
# stattdessen Halte-Aktionen: aktiv solange der Button gedrueckt ist.
IMPULS_DAUER = 0.5

# Aktionen, die per Start/Stop (Halten) statt per Impuls gesteuert werden
HALTE_AKTIONEN = ("auf", "ab")

LED = Pin("LED", Pin.OUT)

AKTIONEN = {
    "auf": Pin(PIN_AUF, Pin.OUT),
    "ab": Pin(PIN_AB, Pin.OUT),
    "stehen": Pin(PIN_STEHEN, Pin.OUT),
    "sitzen": Pin(PIN_SITZEN, Pin.OUT),
}
for _pin in AKTIONEN.values():
    _pin.value(0)

# Zeitpunkt des Skriptstarts (fuer die Laufzeit-Anzeige). Aktuelle IP und
# Netz-Modus ("normal"/"hotspot") liefert pico_tools.wlan (wlan.ip/wlan.modus).
BOOT_ZEIT = time.time()

# Kurzer Verlauf der letzten Aktionen (manuell oder durch die Automatik
# ausgeloest), neueste zuerst
VERLAUF = []
VERLAUF_MAX = 8


def verlauf_eintragen(aktion, quelle):
    VERLAUF.insert(0, {"aktion": aktion, "quelle": quelle, "zeit": time.time()})
    del VERLAUF[VERLAUF_MAX:]


def verlauf_abfragen():
    jetzt = time.time()
    return [
        {
            "aktion": eintrag["aktion"],
            "quelle": eintrag["quelle"],
            "vor_sek": max(0, int(jetzt - eintrag["zeit"])),
        }
        for eintrag in VERLAUF
    ]


def geraeteinfo():
    return {
        "ip": wlan.ip,
        "hostname": GERAETENAME,
        "uptime_sek": int(time.time() - BOOT_ZEIT),
        "version": VERSION,
    }


# ---------------------------------------------------------------------------
# Dateiverwaltung: Dateien auf dem Pico auflisten, lesen und loeschen (zum
# Hochladen/Ueberschreiben bzw. Neuanlegen wird der bestehende /update-
# Endpunkt genutzt - eine gespeicherte Datei ist technisch nichts anderes
# als ein Update auf die jeweils eigene Datei).
# ---------------------------------------------------------------------------

# Dateien, die sich ueber die Dateiverwaltung nicht loeschen lassen, damit
# sich der Pico darueber nicht versehentlich selbst lahmlegt
DATEIEN_GESCHUETZT = ("main.py", "boot.py")


def dateien_auflisten():
    """Alle Dateien im Wurzelverzeichnis mit Groesse, Ordner werden
    uebersprungen (der Pico legt hier ohnehin keine Unterordner an)."""
    ergebnis = []
    for name in os.listdir():
        try:
            eintrag = os.stat(name)
        except OSError:
            continue
        ist_ordner = (eintrag[0] & 0x4000) != 0  # S_IFDIR
        if ist_ordner:
            continue
        ergebnis.append({"name": name, "groesse": eintrag[6]})
    ergebnis.sort(key=lambda e: e["name"])
    return ergebnis


def datei_lesen_text(pfad, max_bytes=UPDATE_MAX_BYTES):
    """Liest eine Datei als Text zum Anzeigen/Bearbeiten. Wirft eine
    Exception bei zu grossen oder nicht-textuellen (binaeren) Dateien -
    der Aufrufer wandelt das in eine 400-Antwort um."""
    groesse = os.stat(pfad)[6]
    if groesse > max_bytes:
        raise ValueError("Datei ist zu gross zum Anzeigen")
    with open(pfad) as f:
        return f.read()


def aktion_ausfuehren(name, quelle="manuell"):
    """Kurzer Impuls fuer Aktionen wie 'stehen'/'sitzen'."""
    pin = AKTIONEN.get(name)
    if pin is None or name in HALTE_AKTIONEN:
        return False
    pin.value(1)
    time.sleep(IMPULS_DAUER)
    pin.value(0)
    verlauf_eintragen(name, quelle)
    return True


def aktion_start(name):
    """Schaltet eine Halte-Aktion ('auf'/'ab') ein, solange der Button gedrueckt ist."""
    pin = AKTIONEN.get(name)
    if pin is None or name not in HALTE_AKTIONEN:
        return False
    pin.value(1)
    verlauf_eintragen(name, "manuell")
    return True


def aktion_stop(name):
    """Schaltet eine Halte-Aktion ('auf'/'ab') wieder aus."""
    pin = AKTIONEN.get(name)
    if pin is None or name not in HALTE_AKTIONEN:
        return False
    pin.value(0)
    return True


# ---------------------------------------------------------------------------
# Automatik: wechselt nach Ablauf der eingestellten Zeit selbststaendig
# zwischen Sitzen und Stehen (laeuft im Hintergrund auf dem zweiten Kern)
# ---------------------------------------------------------------------------

automatik_aktiv = False
automatik_sitzen_sek = 60 * 60
automatik_stehen_sek = 15 * 60
automatik_phase = "sitzen"
automatik_phase_start = 0


def automatik_einschalten(sitzen_min, stehen_min, start_phase="sitzen"):
    global automatik_aktiv, automatik_sitzen_sek, automatik_stehen_sek
    global automatik_phase, automatik_phase_start

    sitzen_min = max(1, float(sitzen_min))
    stehen_min = max(1, float(stehen_min))

    automatik_sitzen_sek = int(sitzen_min * 60)
    automatik_stehen_sek = int(stehen_min * 60)
    automatik_phase = start_phase if start_phase in ("sitzen", "stehen") else "sitzen"
    automatik_phase_start = time.time()
    automatik_aktiv = True
    return True


def automatik_ausschalten():
    global automatik_aktiv
    automatik_aktiv = False
    return True


def automatik_status():
    if not automatik_aktiv:
        return {"aktiv": False}
    dauer = automatik_sitzen_sek if automatik_phase == "sitzen" else automatik_stehen_sek
    rest = dauer - (time.time() - automatik_phase_start)
    return {
        "aktiv": True,
        "phase": automatik_phase,
        "rest_sek": max(0, int(rest)),
        "sitzen_min": automatik_sitzen_sek // 60,
        "stehen_min": automatik_stehen_sek // 60,
    }


def automatik_tick():
    """Prueft, ob die Zeit fuer die aktuelle Automatik-Phase abgelaufen ist."""
    global automatik_phase, automatik_phase_start
    if not automatik_aktiv:
        return
    dauer = automatik_sitzen_sek if automatik_phase == "sitzen" else automatik_stehen_sek
    if time.time() - automatik_phase_start >= dauer:
        neue_phase = "stehen" if automatik_phase == "sitzen" else "sitzen"
        aktion_ausfuehren(neue_phase, "automatik")
        automatik_phase = neue_phase
        automatik_phase_start = time.time()


# ---------------------------------------------------------------------------
# Bewegungserkennung: fragt per Grove Ultrasonic Ranger in einem eigenen,
# von der Automatik-Pruefung entkoppelten Intervall die Distanz ab. Aendert
# sich die Distanz um mindestens den (im Web einstellbaren) Schwellwert
# gegenueber dem letzten Referenzwert, gilt das als Bewegung/Anwesenheit -
# die Automatik wird eingeschaltet und der Abschalt-Timer auf den vollen
# Timeout-Wert zurueckgesetzt. "Kein Echo" (Sensor-Timeout) zaehlt dabei
# ausdruecklich NICHT als Aenderung und ueberschreibt weder den Referenzwert
# noch den zuletzt angezeigten Messwert - im Web bleibt bei einem
# Aussetzer einfach der letzte gueltige cm-Wert stehen. Bleibt eine echte
# Aenderung laenger als der Timeout aus, wird die Automatik ausgeschaltet.
# ---------------------------------------------------------------------------

anwesenheit_aktiv = True
anwesenheit_abfrage_sek = 1          # wie oft der Sensor abgefragt wird
anwesenheit_keine_aenderung_sek = 10 * 60  # Timeout bis zum Abschalten
anwesenheit_schwellwert_cm = 1       # ab dieser Aenderung (cm) gegenueber dem Referenzwert gilt das als Bewegung
anwesenheit_letzte_distanz_cm = None  # letzter GUELTIGER Messwert - bleibt bei "kein Echo" erhalten
anwesenheit_anwesend = False

# Referenzwert, Zeitpunkt der letzten erkannten Aenderung und Zeitpunkt der
# letzten Messung (fuer das vom 1-Sekunden-Haupttick entkoppelte
# Abfrage-Intervall)
anwesenheit_referenz_distanz_cm = None
anwesenheit_letzte_aenderung_zeit = BOOT_ZEIT
anwesenheit_letzte_messung_zeit = 0


def distanz_messen_cm(sig_pin=PIN_SIG_ULTRASCHALL, timeout_us=30_000):
    """Einzelmessung ueber die Single-Wire-Leitung des Grove Ultrasonic
    Ranger. Gibt die Distanz in cm zurueck, oder None bei Timeout
    (kein Objekt in Reichweite bzw. kein Echo empfangen)."""
    trigger = Pin(sig_pin, Pin.OUT)
    trigger.value(0)
    time.sleep_us(2)
    trigger.value(1)
    time.sleep_us(10)  # Trigger-Impuls, Datenblatt verlangt >= 10us
    trigger.value(0)

    echo = Pin(sig_pin, Pin.IN)
    dauer_us = time_pulse_us(echo, 1, timeout_us)
    if dauer_us < 0:
        return None

    return int(dauer_us / 58)  # Laufzeit -> Zentimeter, ganze Zahl (.x wird ignoriert)


def anwesenheit_einstellen(aktiv, abfrage_sek, keine_aenderung_min, schwellwert_cm):
    global anwesenheit_aktiv, anwesenheit_abfrage_sek, anwesenheit_keine_aenderung_sek
    global anwesenheit_schwellwert_cm, anwesenheit_referenz_distanz_cm
    global anwesenheit_letzte_aenderung_zeit, anwesenheit_letzte_messung_zeit

    anwesenheit_abfrage_sek = max(1, float(abfrage_sek))
    anwesenheit_keine_aenderung_sek = max(60, float(keine_aenderung_min) * 60)
    anwesenheit_schwellwert_cm = max(1, float(schwellwert_cm))
    anwesenheit_aktiv = bool(aktiv)
    anwesenheit_referenz_distanz_cm = None  # naechste gueltige Messung setzt neuen Referenzwert
    anwesenheit_letzte_aenderung_zeit = time.time()
    anwesenheit_letzte_messung_zeit = 0  # naechster Tick misst sofort
    return True


def anwesenheit_status():
    rest_sek = anwesenheit_keine_aenderung_sek - (time.time() - anwesenheit_letzte_aenderung_zeit)
    return {
        "aktiv": anwesenheit_aktiv,
        "abfrage_sek": anwesenheit_abfrage_sek,
        "keine_aenderung_min": anwesenheit_keine_aenderung_sek / 60,
        "schwellwert_cm": anwesenheit_schwellwert_cm,
        "distanz_cm": anwesenheit_letzte_distanz_cm,
        "anwesend": anwesenheit_anwesend,
        "rest_sek": max(0, int(rest_sek)) if anwesenheit_aktiv else 0,
    }


def anwesenheit_tick():
    """Wird jede Sekunde aufgerufen, misst aber nur alle
    anwesenheit_abfrage_sek Sekunden tatsaechlich (entkoppeltes Intervall).
    Aendert sich die Distanz um mindestens den Schwellwert gegenueber dem
    Referenzwert, gilt das als Bewegung: die Automatik wird eingeschaltet
    und der Abschalt-Timer auf den vollen Timeout-Wert zurueckgesetzt.
    "Kein Echo" wird nur geloggt, zaehlt aber nicht als Aenderung und
    ueberschreibt weder Referenzwert noch letzten gueltigen Messwert.
    Bleibt eine echte Aenderung laenger als der Timeout aus, wird die
    Automatik wieder ausgeschaltet."""
    global anwesenheit_letzte_distanz_cm, anwesenheit_letzte_messung_zeit
    global anwesenheit_referenz_distanz_cm, anwesenheit_letzte_aenderung_zeit
    global anwesenheit_anwesend

    if not anwesenheit_aktiv:
        return

    jetzt = time.time()
    if jetzt - anwesenheit_letzte_messung_zeit < anwesenheit_abfrage_sek:
        return
    anwesenheit_letzte_messung_zeit = jetzt

    distanz = distanz_messen_cm()
    print("Ultraschall:", "{} cm".format(distanz) if distanz is not None else "kein Echo")

    if distanz is None:
        return  # kein Echo zaehlt nicht als Aenderung - letzter gueltiger Wert bleibt stehen

    anwesenheit_letzte_distanz_cm = distanz

    aenderung_erkannt = (
        anwesenheit_referenz_distanz_cm is not None
        and abs(distanz - anwesenheit_referenz_distanz_cm) >= anwesenheit_schwellwert_cm
    )
    anwesenheit_anwesend = aenderung_erkannt

    if anwesenheit_referenz_distanz_cm is None or aenderung_erkannt:
        anwesenheit_referenz_distanz_cm = distanz
        anwesenheit_letzte_aenderung_zeit = jetzt
        if not automatik_aktiv:
            automatik_einschalten(automatik_sitzen_sek / 60, automatik_stehen_sek / 60, "sitzen")
            verlauf_eintragen("automatik_ein", "sensor")
            print("Bewegung erkannt - Automatik automatisch gestartet")
        return

    if jetzt - anwesenheit_letzte_aenderung_zeit >= anwesenheit_keine_aenderung_sek:
        if automatik_aktiv:
            automatik_ausschalten()
            verlauf_eintragen("automatik_aus", "sensor")
            print(int(anwesenheit_keine_aenderung_sek), "Sek. keine Bewegung - Automatik automatisch gestoppt")
        anwesenheit_letzte_aenderung_zeit = jetzt  # Timer neu starten


# ---------------------------------------------------------------------------
# Geraete-Discovery: Clients (z.B. die Android-App) schicken ein UDP-
# Broadcast-Paket an DISCOVERY_PORT, der Pico antwortet direkt an den
# Absender mit seinen Geraetedaten. Funktioniert sowohl im normalen WLAN als
# auch im eigenen Hotspot, da beides einfach ein IP-Subnetz ist.
# ---------------------------------------------------------------------------

_discovery_socket = None


def discovery_socket_oeffnen():
    global _discovery_socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", DISCOVERY_PORT))
        s.settimeout(0)  # nicht blockierend - wird im Hintergrund-Tick abgefragt
        _discovery_socket = s
    except OSError as exc:
        print("Discovery-Socket konnte nicht gestartet werden:", exc)


def discovery_tick():
    """Beantwortet hoechstens eine Discovery-Anfrage pro Aufruf, ohne den
    Hintergrund-Tick zu blockieren (nicht-blockierender Socket)."""
    if _discovery_socket is None:
        return
    try:
        daten, absender = _discovery_socket.recvfrom(64)
    except OSError:
        return  # kein Paket da (EAGAIN) - normal, kein Fehler
    if daten != DISCOVERY_ANFRAGE:
        return
    antwort = json.dumps({
        "typ": "pico",
        "hostname": GERAETENAME,
        "ip": wlan.ip,
        "modus": wlan.modus,
        "version": VERSION,
    })
    try:
        _discovery_socket.sendto(antwort.encode("utf-8"), absender)
    except OSError:
        pass


def hintergrund_thread():
    """Laeuft dauerhaft auf dem zweiten Kern und kuemmert sich um Automatik,
    Bewegungserkennung und Discovery (RP2040 kann nur einen zusaetzlichen
    _thread gleichzeitig ausfuehren, daher alles in einer Schleife statt in
    getrennten Threads)."""
    while True:
        automatik_tick()
        anwesenheit_tick()
        discovery_tick()
        time.sleep(1)


# ---------------------------------------------------------------------------
# Webseite (modernes UI, 2x2 Button-Grid)
# ---------------------------------------------------------------------------

INDEX_DATEI = "index.html"
EINSTELLUNGEN_DATEI = "einstellungen.html"
DATEIEN_DATEI = "dateien.html"


def datei_senden(client, pfad_datei):
    """Streamt eine HTML-Datei in kleinen Stuecken vom Dateisystem, statt den
    kompletten Inhalt dauerhaft als String im RAM zu halten (main.py bleibt
    dadurch klein genug, um sich per Web-Update selbst syntaktisch pruefen
    zu koennen, ohne dass der Pico dabei aus dem Speicher laeuft)."""
    groesse = os.stat(pfad_datei)[6]
    header = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Length: {laenge}\r\n"
        "Connection: close\r\n\r\n"
    ).format(laenge=groesse)
    _sende_alles(client, header.encode("utf-8"))
    with open(pfad_datei, "rb") as f:
        while True:
            stueck = f.read(512)
            if not stueck:
                break
            _sende_alles(client, stueck)


# ---------------------------------------------------------------------------
# Webserver
# ---------------------------------------------------------------------------

def http_antwort(client, status, inhalt, content_type="text/html; charset=utf-8"):
    body = inhalt.encode("utf-8") if isinstance(inhalt, str) else inhalt
    header = (
        "HTTP/1.1 {status}\r\n"
        "Content-Type: {ctype}\r\n"
        "Content-Length: {length}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status=status, ctype=content_type, length=len(body))
    _sende_alles(client, header.encode("utf-8"))
    _sende_alles(client, body)


def _sende_alles(client, daten):
    """client.send() verschickt bei groesseren Antworten oft nur einen Teil
    auf einmal - der Rest muss in einer Schleife nachgesendet werden, sonst
    kommt beim Browser eine abgeschnittene Antwort an."""
    ansicht = memoryview(daten)
    gesendet = 0
    gesamt = len(ansicht)
    while gesendet < gesamt:
        n = client.send(ansicht[gesendet:])
        if not n:
            break
        gesendet += n


def query_parsen(pfad):
    """Zerlegt '/pfad?a=1&b=2' in ein Dict {'a': '1', 'b': '2'}."""
    if "?" not in pfad:
        return {}
    query = pfad.split("?", 1)[1]
    ergebnis = {}
    for teil in query.split("&"):
        if "=" in teil:
            schluessel, wert = teil.split("=", 1)
            ergebnis[schluessel] = wert
    return ergebnis


def header_wert(kopf_text, name):
    praefix = name.lower() + ":"
    for zeile in kopf_text.split("\r\n"):
        if zeile.lower().startswith(praefix):
            return zeile.split(":", 1)[1].strip()
    return None


def anfrage_bearbeiten(client):
    try:
        client.settimeout(15)
        puffer = client.recv(1536)
        if not puffer:
            client.close()
            return
        # Falls die Kopfzeilen noch nicht vollstaendig da sind, weiterlesen
        while b"\r\n\r\n" not in puffer and len(puffer) < 4096:
            stueck = client.recv(1536)
            if not stueck:
                break
            puffer += stueck

        trenner = puffer.find(b"\r\n\r\n")
        if trenner == -1:
            kopf_text = puffer.decode("utf-8", "ignore")
            body_bereits_gelesen = b""
        else:
            kopf_text = puffer[:trenner].decode("utf-8", "ignore")
            body_bereits_gelesen = puffer[trenner + 4:]

        erste_zeile = kopf_text.split("\r\n", 1)[0]
        teile = erste_zeile.split(" ")
        methode = teile[0] if teile else "GET"
        pfad = teile[1] if len(teile) > 1 else "/"

        if methode == "POST" and pfad.startswith("/update"):
            update.anfrage_bearbeiten(
                client, pfad, lambda name: header_wert(kopf_text, name),
                body_bereits_gelesen, http_antwort, max_bytes=UPDATE_MAX_BYTES,
            )
        elif methode == "POST" and pfad.startswith("/wlan/speichern"):
            wlan.speichern_anfrage_bearbeiten(
                client, lambda name: header_wert(kopf_text, name),
                body_bereits_gelesen, http_antwort,
            )
        elif pfad.startswith("/wlan/status"):
            http_antwort(client, "200 OK", json.dumps(wlan.status()), "application/json")
        elif pfad.startswith("/neustart"):
            http_antwort(client, "200 OK", json.dumps({"ok": True}), "application/json")
            client.close()
            time.sleep(0.5)
            machine.reset()
        elif pfad.startswith("/info"):
            http_antwort(client, "200 OK", json.dumps(geraeteinfo()), "application/json")
        elif pfad.startswith("/verlauf"):
            http_antwort(client, "200 OK", json.dumps(verlauf_abfragen()), "application/json")
        elif pfad.startswith("/automatik/start"):
            parameter = query_parsen(pfad)
            try:
                automatik_einschalten(
                    parameter.get("sitzen", "60"),
                    parameter.get("stehen", "15"),
                    parameter.get("phase", "sitzen"),
                )
                http_antwort(client, "200 OK", json.dumps(automatik_status()), "application/json")
            except (ValueError, TypeError):
                http_antwort(client, "400 Bad Request", json.dumps({"aktiv": False}), "application/json")
        elif pfad.startswith("/automatik/stop"):
            automatik_ausschalten()
            http_antwort(client, "200 OK", json.dumps(automatik_status()), "application/json")
        elif pfad.startswith("/automatik/status"):
            http_antwort(client, "200 OK", json.dumps(automatik_status()), "application/json")
        elif pfad.startswith("/anwesenheit/start"):
            parameter = query_parsen(pfad)
            try:
                anwesenheit_einstellen(
                    True,
                    parameter.get("abfrage", "1"),
                    parameter.get("timeout", "10"),
                    parameter.get("schwellwert", "1"),
                )
                http_antwort(client, "200 OK", json.dumps(anwesenheit_status()), "application/json")
            except (ValueError, TypeError):
                http_antwort(client, "400 Bad Request", json.dumps({"aktiv": False}), "application/json")
        elif pfad.startswith("/anwesenheit/stop"):
            anwesenheit_einstellen(
                False,
                anwesenheit_abfrage_sek,
                anwesenheit_keine_aenderung_sek / 60,
                anwesenheit_schwellwert_cm,
            )
            http_antwort(client, "200 OK", json.dumps(anwesenheit_status()), "application/json")
        elif pfad.startswith("/anwesenheit/status"):
            http_antwort(client, "200 OK", json.dumps(anwesenheit_status()), "application/json")
        elif pfad.startswith("/aktion/"):
            name = pfad.split("/aktion/", 1)[1].split("?")[0]
            erfolg = aktion_ausfuehren(name)
            http_antwort(
                client,
                "200 OK" if erfolg else "404 Not Found",
                json.dumps({"ok": erfolg, "aktion": name}),
                "application/json",
            )
        elif pfad.startswith("/start/"):
            name = pfad.split("/start/", 1)[1].split("?")[0]
            erfolg = aktion_start(name)
            http_antwort(
                client,
                "200 OK" if erfolg else "404 Not Found",
                json.dumps({"ok": erfolg, "aktion": name}),
                "application/json",
            )
        elif pfad.startswith("/stop/"):
            name = pfad.split("/stop/", 1)[1].split("?")[0]
            erfolg = aktion_stop(name)
            http_antwort(
                client,
                "200 OK" if erfolg else "404 Not Found",
                json.dumps({"ok": erfolg, "aktion": name}),
                "application/json",
            )
        elif pfad.startswith("/dateien/liste"):
            http_antwort(client, "200 OK", json.dumps(dateien_auflisten()), "application/json")
        elif pfad.startswith("/dateien/lesen"):
            parameter = query_parsen(pfad)
            name = parameter.get("name", "")
            if not update.ziel_gueltig(name):
                http_antwort(
                    client, "400 Bad Request",
                    json.dumps({"ok": False, "fehler": "Ungueltiger Dateiname"}),
                    "application/json",
                )
            else:
                try:
                    inhalt = datei_lesen_text(name)
                    http_antwort(client, "200 OK", inhalt, "text/plain; charset=utf-8")
                except OSError:
                    http_antwort(
                        client, "404 Not Found",
                        json.dumps({"ok": False, "fehler": "Datei nicht gefunden"}),
                        "application/json",
                    )
                except Exception as exc:
                    http_antwort(
                        client, "400 Bad Request",
                        json.dumps({"ok": False, "fehler": str(exc)}),
                        "application/json",
                    )
        elif pfad.startswith("/dateien/loeschen"):
            parameter = query_parsen(pfad)
            name = parameter.get("name", "")
            if not update.ziel_gueltig(name):
                http_antwort(
                    client, "400 Bad Request",
                    json.dumps({"ok": False, "fehler": "Ungueltiger Dateiname"}),
                    "application/json",
                )
            elif name in DATEIEN_GESCHUETZT:
                http_antwort(
                    client, "400 Bad Request",
                    json.dumps({"ok": False, "fehler": name + " kann nicht geloescht werden"}),
                    "application/json",
                )
            else:
                try:
                    os.remove(name)
                    http_antwort(client, "200 OK", json.dumps({"ok": True}), "application/json")
                except OSError as exc:
                    http_antwort(
                        client, "404 Not Found",
                        json.dumps({"ok": False, "fehler": str(exc)}),
                        "application/json",
                    )
        elif pfad == "/dateien" or pfad == "/dateien.html":
            datei_senden(client, DATEIEN_DATEI)
        elif pfad == "/einstellungen" or pfad == "/einstellungen.html":
            datei_senden(client, EINSTELLUNGEN_DATEI)
        elif pfad == "/":
            # Im Hotspot-Modus (WLAN-Verbindung fehlgeschlagen) landet man
            # direkt auf der Recovery-/Einstellungen-Seite. /index.html
            # bleibt trotzdem erreichbar, damit die Steuerung auch ueber den
            # Hotspot direkt nutzbar ist.
            datei_senden(client, EINSTELLUNGEN_DATEI if wlan.modus == "hotspot" else INDEX_DATEI)
        elif pfad == "/index.html":
            datei_senden(client, INDEX_DATEI)
        else:
            http_antwort(client, "404 Not Found", "Nicht gefunden")
    except Exception as exc:
        print("Fehler bei der Anfragebearbeitung:", exc)
    finally:
        client.close()
        gc.collect()


def webserver_starten(port=80):
    adresse = socket.getaddrinfo("0.0.0.0", port)[0][-1]
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(adresse)
    server.listen(4)
    print("Webserver laeuft auf Port", port)

    while True:
        client, addr = server.accept()
        anfrage_bearbeiten(client)


def main():
    discovery_socket_oeffnen()

    wlan.AP_SSID = AP_SSID
    wlan.AP_PASSWORT = AP_PASSWORT
    wlan.HOTSPOT_TIMEOUT_SEK = HOTSPOT_TIMEOUT_SEK
    wlan.LED = LED

    _netz, modus = wlan.verbinden(
        hostname=GERAETENAME,
        standard_ssid=WLAN_SSID,
        standard_passwort=WLAN_PASSWORT,
        versuche=WLAN_MAX_VERSUCHE,
        timeout=WLAN_VERSUCH_TIMEOUT,
    )

    if modus == "normal":
        _thread.start_new_thread(hintergrund_thread, ())
    else:
        _thread.start_new_thread(wlan.hotspot_timeout_thread, (discovery_tick,))

    webserver_starten()


if __name__ == "__main__":
    main()

