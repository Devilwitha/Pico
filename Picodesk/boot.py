"""
boot.py - Absicherung fuer automatische GitHub-Updates

Laeuft vor main.py. Liegt "update_ausstehend.json" vor, hat
pico_tools/github_update.py gerade ein Update installiert, das main.py noch
nicht per github_update.bestaetigen() als gelungen gemeldet hat. Dann:

  - startet der Pico mehr als MAX_STARTS-mal ohne Bestaetigung, oder
  - bestaetigt main.py nicht innerhalb von BESTAETIGUNG_TIMEOUT_MS (z.B. weil
    main.py mit einer Exception abbricht und in der REPL stehen bleibt),

wird das Update zurueckgenommen: "<datei>.bak" wird wiederhergestellt, neu
angelegte Dateien werden geloescht, und der Commit landet in
"update_abgelehnt.txt", damit er nicht automatisch erneut installiert wird
(manuell ueber "Jetzt aktualisieren" geht es trotzdem).

Bewusst ohne Abhaengigkeit von pico_tools, damit das auch funktioniert,
wenn genau diese Module durch das Update kaputt sind.
"""

import json
import os
import machine

AUSSTEHEND_DATEI = "update_ausstehend.json"
ABGELEHNT_DATEI = "update_abgelehnt.txt"
MAX_STARTS = 3
BESTAETIGUNG_TIMEOUT_MS = 3 * 60 * 1000


def _update_zuruecknehmen():
    try:
        with open(AUSSTEHEND_DATEI) as f:
            daten = json.load(f)
    except (OSError, ValueError):
        daten = {}
    neu = daten.get("neu", ())
    for pfad in daten.get("dateien", ()):
        if pfad in neu:
            try:
                os.remove(pfad)
            except OSError:
                pass
            continue
        try:
            os.stat(pfad + ".bak")
        except OSError:
            continue
        try:
            os.remove(pfad)
        except OSError:
            pass
        os.rename(pfad + ".bak", pfad)
    try:
        with open(ABGELEHNT_DATEI, "w") as f:
            f.write(daten.get("commit", ""))
    except OSError:
        pass
    try:
        os.remove(AUSSTEHEND_DATEI)
    except OSError:
        pass
    print("GitHub-Update zurueckgenommen")


def _bestaetigung_abgelaufen(_timer):
    try:
        os.stat(AUSSTEHEND_DATEI)
    except OSError:
        return  # main.py hat bestaetigt
    _update_zuruecknehmen()
    machine.reset()


def _update_pruefen():
    try:
        with open(AUSSTEHEND_DATEI) as f:
            daten = json.load(f)
    except OSError:
        return None  # kein Update ausstehend - Normalfall
    except ValueError:
        daten = {}
    starts = daten.get("starts", 0) + 1
    if starts > MAX_STARTS:
        _update_zuruecknehmen()
        return None
    daten["starts"] = starts
    with open(AUSSTEHEND_DATEI, "w") as f:
        json.dump(daten, f)
    print("GitHub-Update ausstehend - warte auf Bestaetigung durch main.py")
    return machine.Timer(
        mode=machine.Timer.ONE_SHOT,
        period=BESTAETIGUNG_TIMEOUT_MS,
        callback=_bestaetigung_abgelaufen,
    )


try:
    _update_timer = _update_pruefen()
except Exception as _exc:
    print("boot.py: Update-Pruefung fehlgeschlagen:", _exc)
