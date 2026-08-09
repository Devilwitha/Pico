"""
pico_tools/update.py - Datei-Update per HTTP-Upload (wiederverwendbar)

Nimmt eine per POST hochgeladene Datei entgegen, schreibt sie zunaechst in
eine temporaere Datei ("<ziel>.neu") statt sie komplett im RAM zu halten,
prueft sie danach (bei ".py"-Dateien per compile() auf gueltiges Python,
sonst nur auf nicht-leeren Inhalt) und ersetzt erst bei Erfolg die
Zieldatei - die vorherige Version bleibt dabei als "<ziel>.bak" erhalten.
Der Dateiname wird 1:1 uebernommen, ein Update ersetzt also nur eine exakt
gleichnamige Datei (oder legt eine neue an), ohne andere Dateien
anzufassen. Ziel darf ein Dateiname im Wurzelverzeichnis sein oder genau
einen Unterordner-Level enthalten (z.B. "pico_tools/wlan.py") - der Ordner
wird bei Bedarf automatisch angelegt.

Verwendung im Webserver-Dispatcher (main.py):

    from pico_tools import update

    elif methode == "POST" and pfad.startswith("/update"):
        update.anfrage_bearbeiten(
            client, pfad, lambda name: header_wert(kopf_text, name),
            body_bereits_gelesen, http_antwort,
        )

Erwartete Aufruf-Konvention (passend zu main.py):
  POST /update?ziel=<dateiname>, Body = kompletter Dateiinhalt.
  Antwort: {"ok": bool, "neustart": bool} bzw. {"ok": False, "fehler": "..."}.
  ".py"-Updates starten das Geraet automatisch neu (main.py und andere
  Module muessen neu importiert/ausgefuehrt werden).
"""

import gc
import os
import time
import json
import machine

# Maximale Groesse einer Update-Datei in Bytes (Sicherheitsgrenze)
MAX_BYTES = 400_000


def ziel_gueltig(ziel):
    """Erlaubt ist ein Dateiname im Wurzelverzeichnis oder genau ein
    Unterordner-Level (z.B. "pico_tools/wlan.py") - abgesichert gegen
    Pfad-Traversal ('..', '\\', fuehrender Punkt, leere Teile). Der Name
    der hochgeladenen Datei wird sonst unveraendert uebernommen."""
    if not ziel:
        return False
    teile = ziel.split("/")
    if len(teile) > 2:
        return False
    for teil in teile:
        if not teil or teil.startswith(".") or ".." in teil or "\\" in teil:
            return False
    return True


def temp_dateiname(ziel):
    return ziel + ".neu"


def backup_dateiname(ziel):
    return ziel + ".bak"


def ordner_sicherstellen(ziel):
    """Legt bei Bedarf den (einen) Unterordner von ziel an, z.B. fuer
    "pico_tools/wlan.py" den Ordner "pico_tools"."""
    if "/" not in ziel:
        return
    ordner = ziel.rsplit("/", 1)[0]
    try:
        os.mkdir(ordner)
    except OSError:
        pass  # existiert bereits


def empfangen(temp_datei, client, laenge, bereits_gelesen):
    """Liest genau `laenge` Bytes vom Socket und schreibt sie direkt (in
    kleinen Stuecken) in eine temporaere Datei, statt alles im RAM zu
    halten."""
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


def pruefen(pfad, python_pruefen):
    """Prueft die hochgeladene Datei, bevor das Ziel ueberschrieben wird:
    bei .py-Dateien per compile() auf gueltiges Python, sonst nur auf
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


def uebernehmen(ziel, temp_datei, backup_datei):
    """Ersetzt die Zieldatei durch die neue Version, alte Version bleibt
    als Backup."""
    try:
        os.remove(backup_datei)
    except OSError:
        pass
    try:
        os.rename(ziel, backup_datei)
    except OSError:
        pass
    os.rename(temp_datei, ziel)


def aufraeumen(temp_datei):
    try:
        os.remove(temp_datei)
    except OSError:
        pass


def _query_parsen(pfad):
    if "?" not in pfad:
        return {}
    query = pfad.split("?", 1)[1]
    ergebnis = {}
    for teil in query.split("&"):
        if "=" in teil:
            schluessel, wert = teil.split("=", 1)
            ergebnis[schluessel] = wert
    return ergebnis


def anfrage_bearbeiten(client, pfad, header_wert, body_bereits_gelesen, http_antwort, max_bytes=None):
    """Behandelt eine komplette POST /update-Anfrage: liest die Datei,
    prueft sie und uebernimmt sie bei Erfolg (siehe Modul-Docstring).
    Startet das Geraet bei .py-Updates automatisch neu.

    header_wert: Funktion, die einen Header-Namen entgegennimmt und dessen
    Wert (oder None) liefert, z.B. lambda name: header_wert(kopf_text, name).
    http_antwort: Funktion http_antwort(client, status, inhalt, content_type).
    """
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    parameter = _query_parsen(pfad)
    ziel = parameter.get("ziel", "")

    laenge_text = header_wert("Content-Length")
    laenge = int(laenge_text) if laenge_text and laenge_text.isdigit() else 0

    if not ziel_gueltig(ziel):
        http_antwort(
            client, "400 Bad Request",
            json.dumps({"ok": False, "fehler": "Ungueltiger Dateiname"}),
            "application/json",
        )
        return
    if laenge <= 0 or laenge > max_bytes:
        http_antwort(
            client, "400 Bad Request",
            json.dumps({"ok": False, "fehler": "Datei fehlt oder ist zu gross"}),
            "application/json",
        )
        return

    python_pruefen = ziel.endswith(".py")
    neustart = python_pruefen  # main.py und andere Module brauchen einen Neustart
    temp_datei = temp_dateiname(ziel)
    backup_datei = backup_dateiname(ziel)

    ordner_sicherstellen(ziel)

    vollstaendig = empfangen(temp_datei, client, laenge, body_bereits_gelesen)
    if not vollstaendig:
        aufraeumen(temp_datei)
        http_antwort(
            client, "400 Bad Request",
            json.dumps({"ok": False, "fehler": "Uebertragung unvollstaendig"}),
            "application/json",
        )
        return

    gueltig, fehler = pruefen(temp_datei, python_pruefen)
    if not gueltig:
        aufraeumen(temp_datei)
        http_antwort(
            client, "400 Bad Request",
            json.dumps({"ok": False, "fehler": fehler}),
            "application/json",
        )
        return

    uebernehmen(ziel, temp_datei, backup_datei)
    http_antwort(
        client, "200 OK",
        json.dumps({"ok": True, "neustart": neustart}),
        "application/json",
    )
    if neustart:
        client.close()
        time.sleep(0.5)
        machine.reset()
