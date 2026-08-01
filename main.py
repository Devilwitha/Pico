"""
Pico W Steuerung - WLAN Webserver mit 4 Tasten (Auf / Ab / Stehen / Sitzen)

Verbindet sich mit einem bestehenden WLAN und startet einen Webserver mit
moderner Oberflaeche. Ueber vier Buttons koennen GPIO-Ausgaenge angesteuert
werden (z.B. fuer Relais, Motorsteuerung o.ae.). Enthaelt ausserdem eine
Automatik (zeitgesteuerter Wechsel Sitzen/Stehen), eine Anwesenheitserkennung
per Grove Ultrasonic Ranger (aktiviert die Automatik automatisch, sobald ein
Objekt naeher als ein im Web einstellbarer Schwellwert ist) und eine
WLAN-Update-Funktion: im Browser eine neue Version dieser Datei hochladen,
der Pico prueft sie auf gueltiges Python, ersetzt main.py und startet neu.

Einfach WLAN_SSID / WLAN_PASSWORT unten eintragen (oder eine Datei
"wlan.conf" mit {"ssid": "...", "password": "..."} neben dieses Skript
legen) und als main.py auf den Pico W kopieren.

Verkabelung Grove Ultrasonic Ranger (siehe PIN_SIG_ULTRASCHALL unten):
  Schwarz (GND) -> GND
  Rot     (VCC) -> 3V3 OUT (nicht an 5V/VBUS, GPIOs sind nicht 5V-tolerant!)
  Gelb    (SIG) -> GPIO aus PIN_SIG_ULTRASCHALL
  Weiss   (NC)  -> nicht anschliessen
"""

import network
import socket
import time
import json
import gc
import os
import machine
import _thread
from machine import Pin, time_pulse_us

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

VERSION = "1.1.0"

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

# Zeitpunkt des Skriptstarts (fuer die Laufzeit-Anzeige) und die zuletzt
# vergebene IP-Adresse (fuer die Info-Zeile im Web-UI)
BOOT_ZEIT = time.time()
AKTUELLE_IP = ""

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
        "ip": AKTUELLE_IP,
        "hostname": GERAETENAME,
        "uptime_sek": int(time.time() - BOOT_ZEIT),
        "version": VERSION,
    }


# ---------------------------------------------------------------------------
# WLAN-Update: neue Skript-Version per Datei-Upload einspielen
# ---------------------------------------------------------------------------

# Erlaubte Update-Ziele (Whitelist gegen beliebige Zielpfade). "python_pruefen"
# steuert, ob die Datei per compile() als gueltiges Python geprueft wird
# (nur main.py), "neustart" ob der Pico danach neu startet (index.html wird
# beim naechsten Aufruf einfach frisch von der Platte gelesen, kein Reset noetig).
UPDATE_ZIELE = {
    "main.py": {"temp": "main_neu.py", "backup": "main_backup.py", "python_pruefen": True, "neustart": True},
    "index.html": {"temp": "index_neu.html", "backup": "index_backup.html", "python_pruefen": False, "neustart": False},
}


def update_empfangen(temp_datei, client, laenge, bereits_gelesen):
    """Liest genau `laenge` Bytes vom Socket und schreibt sie direkt (in
    kleinen Stuecken) in eine temporaere Datei, statt alles im RAM zu halten."""
    geschrieben = 0
    with open(temp_datei, "wb") as f:
        if bereits_gelesen:
            f.write(bereits_gelesen)
            geschrieben += len(bereits_gelesen)
        while geschrieben < laenge:
            stueck = client.recv(min(1024, laenge - geschrieben))
            if not stueck:
                break
            f.write(stueck)
            geschrieben += len(stueck)
    return geschrieben == laenge


def update_pruefen(pfad, python_pruefen):
    """Prueft die hochgeladene Datei, bevor das Ziel ueberschrieben wird:
    bei main.py per compile() auf gueltiges Python, sonst nur auf
    nicht-leeren Inhalt. gc.collect() davor schafft moeglichst viel
    zusammenhaengenden freien Speicher, da compile() kurzzeitig deutlich
    mehr RAM braucht als die Dateigroesse selbst."""
    gc.collect()
    try:
        with open(pfad) as f:
            inhalt = f.read()
        if not inhalt.strip():
            return False, "Datei ist leer"
        if python_pruefen:
            gc.collect()
            compile(inhalt, pfad, "exec")
        return True, None
    except Exception as exc:
        praefix = "Ungueltiges Python: " if python_pruefen else "Fehler: "
        return False, praefix + str(exc)


def update_uebernehmen(ziel, konfig):
    """Ersetzt die Zieldatei durch die neue Version, alte Version bleibt als Backup."""
    try:
        os.remove(konfig["backup"])
    except OSError:
        pass
    try:
        os.rename(ziel, konfig["backup"])
    except OSError:
        pass
    os.rename(konfig["temp"], ziel)


def update_aufraeumen(temp_datei):
    try:
        os.remove(temp_datei)
    except OSError:
        pass


def lade_wlan_zugangsdaten():
    """Liest ssid/password aus wlan.conf falls vorhanden, sonst Konstanten oben."""
    try:
        with open("wlan.conf") as f:
            daten = json.load(f)
            ssid = daten.get("ssid") or WLAN_SSID
            passwort = daten.get("password") or WLAN_PASSWORT
            return ssid, passwort
    except OSError:
        return WLAN_SSID, WLAN_PASSWORT


def hostname_setzen(name):
    """Setzt den DHCP-Hostnamen, ueber den der Pico im lokalen Netz sichtbar ist."""
    try:
        network.hostname(name)
    except (AttributeError, OSError):
        pass


def mit_wlan_verbinden(ssid, passwort, timeout=20):
    global AKTUELLE_IP
    hostname_setzen(GERAETENAME)

    wlan = network.WLAN(network.STA_IF)
    try:
        wlan.config(hostname=GERAETENAME)
    except (ValueError, OSError):
        pass
    wlan.active(True)
    wlan.connect(ssid, passwort)

    start = time.time()
    while not wlan.isconnected():
        if time.time() - start > timeout:
            raise RuntimeError("WLAN-Verbindung fehlgeschlagen (Timeout)")
        LED.toggle()
        time.sleep(0.3)

    LED.value(1)
    ip = wlan.ifconfig()[0]
    AKTUELLE_IP = ip
    print("Mit WLAN verbunden, IP-Adresse:", ip, "- Hostname:", GERAETENAME)
    return wlan


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
automatik_sitzen_sek = 90 * 60
automatik_stehen_sek = 30 * 60
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
# sich die Distanz (Bewegung), wird die Automatik eingeschaltet. Bleibt sie
# laenger als der Timeout unveraendert, wird die Automatik ausgeschaltet.
# Beide Zeiten sind im Web einstellbar.
# ---------------------------------------------------------------------------

# Ab wie viel cm Unterschied zur letzten Referenzmessung eine "Aenderung"
# (Bewegung) zaehlt - Toleranz gegen normales Sensor-Rauschen
ANWESENHEIT_AENDERUNG_TOLERANZ_CM = 3

anwesenheit_aktiv = False
anwesenheit_abfrage_sek = 3          # wie oft der Sensor abgefragt wird
anwesenheit_keine_aenderung_sek = 10 * 60  # Timeout bis zum Abschalten
anwesenheit_letzte_distanz_cm = None

# Referenzwert, Zeitpunkt der letzten Aenderung und Zeitpunkt der letzten
# Messung (fuer das vom 1-Sekunden-Haupttick entkoppelte Abfrage-Intervall)
anwesenheit_referenz_distanz_cm = None
anwesenheit_letzte_aenderung_zeit = 0
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


def anwesenheit_einstellen(aktiv, abfrage_sek, keine_aenderung_min):
    global anwesenheit_aktiv, anwesenheit_abfrage_sek, anwesenheit_keine_aenderung_sek
    global anwesenheit_referenz_distanz_cm, anwesenheit_letzte_aenderung_zeit
    global anwesenheit_letzte_messung_zeit

    anwesenheit_abfrage_sek = max(1, float(abfrage_sek))
    anwesenheit_keine_aenderung_sek = max(60, float(keine_aenderung_min) * 60)
    anwesenheit_aktiv = bool(aktiv)
    anwesenheit_referenz_distanz_cm = None
    anwesenheit_letzte_aenderung_zeit = time.time()
    anwesenheit_letzte_messung_zeit = 0  # naechster Tick misst sofort
    return True


def anwesenheit_status():
    rest_sek = anwesenheit_keine_aenderung_sek - (time.time() - anwesenheit_letzte_aenderung_zeit)
    return {
        "aktiv": anwesenheit_aktiv,
        "abfrage_sek": anwesenheit_abfrage_sek,
        "keine_aenderung_min": anwesenheit_keine_aenderung_sek / 60,
        "distanz_cm": anwesenheit_letzte_distanz_cm,
        "rest_sek": max(0, int(rest_sek)) if anwesenheit_aktiv else 0,
    }


def anwesenheit_tick():
    """Wird jede Sekunde aufgerufen, misst aber nur alle
    anwesenheit_abfrage_sek Sekunden tatsaechlich (entkoppeltes Intervall).
    Bewegung (Distanzaenderung) schaltet die Automatik ein, laengerer
    Stillstand (Timeout) schaltet sie wieder aus."""
    global anwesenheit_letzte_distanz_cm, anwesenheit_letzte_messung_zeit
    global anwesenheit_referenz_distanz_cm, anwesenheit_letzte_aenderung_zeit

    if not anwesenheit_aktiv:
        return

    jetzt = time.time()
    if jetzt - anwesenheit_letzte_messung_zeit < anwesenheit_abfrage_sek:
        return
    anwesenheit_letzte_messung_zeit = jetzt

    distanz = distanz_messen_cm()
    anwesenheit_letzte_distanz_cm = distanz
    print("Ultraschall:", "{} cm".format(distanz) if distanz is not None else "kein Echo")

    if distanz is None:
        return  # kein Echo liefert keinen verwertbaren Vergleichswert

    aenderung_erkannt = (
        anwesenheit_referenz_distanz_cm is not None
        and abs(distanz - anwesenheit_referenz_distanz_cm) > ANWESENHEIT_AENDERUNG_TOLERANZ_CM
    )

    if anwesenheit_referenz_distanz_cm is None or aenderung_erkannt:
        anwesenheit_referenz_distanz_cm = distanz
        anwesenheit_letzte_aenderung_zeit = jetzt
        if aenderung_erkannt and not automatik_aktiv:
            automatik_einschalten(automatik_sitzen_sek / 60, automatik_stehen_sek / 60, "sitzen")
            print("Bewegung erkannt - Automatik automatisch gestartet")
        return

    if jetzt - anwesenheit_letzte_aenderung_zeit >= anwesenheit_keine_aenderung_sek:
        if automatik_aktiv:
            automatik_ausschalten()
            print(int(anwesenheit_keine_aenderung_sek), "Sek. keine Bewegung - Automatik automatisch gestoppt")
        anwesenheit_letzte_aenderung_zeit = jetzt  # Timer neu starten


def hintergrund_thread():
    """Laeuft dauerhaft auf dem zweiten Kern und kuemmert sich sowohl um
    die Automatik als auch um die Bewegungserkennung (RP2040 kann nur einen
    zusaetzlichen _thread gleichzeitig ausfuehren, daher beides in einer
    Schleife statt in zwei getrennten Threads)."""
    while True:
        automatik_tick()
        anwesenheit_tick()
        time.sleep(1)


# ---------------------------------------------------------------------------
# Webseite (modernes UI, 2x2 Button-Grid)
# ---------------------------------------------------------------------------

INDEX_DATEI = "index.html"


def index_seite_senden(client):
    """Streamt index.html in kleinen Stuecken vom Dateisystem, statt den
    kompletten Inhalt dauerhaft als String im RAM zu halten (main.py bleibt
    dadurch klein genug, um sich per Web-Update selbst syntaktisch pruefen
    zu koennen, ohne dass der Pico dabei aus dem Speicher laeuft)."""
    groesse = os.stat(INDEX_DATEI)[6]
    header = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Length: {laenge}\r\n"
        "Connection: close\r\n\r\n"
    ).format(laenge=groesse)
    _sende_alles(client, header.encode("utf-8"))
    with open(INDEX_DATEI, "rb") as f:
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
            parameter = query_parsen(pfad)
            ziel = parameter.get("ziel", "main.py")
            konfig = UPDATE_ZIELE.get(ziel)

            laenge_text = header_wert(kopf_text, "Content-Length")
            laenge = int(laenge_text) if laenge_text and laenge_text.isdigit() else 0

            if konfig is None:
                http_antwort(
                    client, "400 Bad Request",
                    json.dumps({"ok": False, "fehler": "Unbekanntes Update-Ziel"}),
                    "application/json",
                )
            elif laenge <= 0 or laenge > UPDATE_MAX_BYTES:
                http_antwort(
                    client, "400 Bad Request",
                    json.dumps({"ok": False, "fehler": "Datei fehlt oder ist zu gross"}),
                    "application/json",
                )
            else:
                vollstaendig = update_empfangen(konfig["temp"], client, laenge, body_bereits_gelesen)
                if not vollstaendig:
                    update_aufraeumen(konfig["temp"])
                    http_antwort(
                        client, "400 Bad Request",
                        json.dumps({"ok": False, "fehler": "Uebertragung unvollstaendig"}),
                        "application/json",
                    )
                else:
                    gueltig, fehler = update_pruefen(konfig["temp"], konfig["python_pruefen"])
                    if not gueltig:
                        update_aufraeumen(konfig["temp"])
                        http_antwort(
                            client, "400 Bad Request",
                            json.dumps({"ok": False, "fehler": fehler}),
                            "application/json",
                        )
                    else:
                        update_uebernehmen(ziel, konfig)
                        http_antwort(
                            client, "200 OK",
                            json.dumps({"ok": True, "neustart": konfig["neustart"]}),
                            "application/json",
                        )
                        if konfig["neustart"]:
                            client.close()
                            time.sleep(0.5)
                            machine.reset()
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
                    parameter.get("sitzen", "90"),
                    parameter.get("stehen", "30"),
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
                    parameter.get("abfrage", "3"),
                    parameter.get("timeout", "10"),
                )
                http_antwort(client, "200 OK", json.dumps(anwesenheit_status()), "application/json")
            except (ValueError, TypeError):
                http_antwort(client, "400 Bad Request", json.dumps({"aktiv": False}), "application/json")
        elif pfad.startswith("/anwesenheit/stop"):
            anwesenheit_einstellen(False, anwesenheit_abfrage_sek, anwesenheit_keine_aenderung_sek / 60)
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
        elif pfad == "/" or pfad == "/index.html":
            index_seite_senden(client)
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
    ssid, passwort = lade_wlan_zugangsdaten()
    if not ssid:
        raise RuntimeError(
            "Kein WLAN konfiguriert. Bitte WLAN_SSID/WLAN_PASSWORT im Skript "
            "setzen oder eine wlan.conf mit ssid/password anlegen."
        )
    mit_wlan_verbinden(ssid, passwort)
    _thread.start_new_thread(hintergrund_thread, ())
    webserver_starten()


if __name__ == "__main__":
    main()

