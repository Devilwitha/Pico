"""
pico_tools/github_update.py - Automatisches Update aus einem GitHub-Repository
(wiederverwendbar)

Fragt in einem festen Intervall den neuesten Commit eines Zweigs ab (eine
kleine Anfrage, die nur den Commit-Hash liefert). Hat sich dieser seit dem
letzten Update geaendert, wird das Manifest (Standard "pico_dateien.txt",
eine Datei pro Zeile, "#" fuer Kommentare) aus diesem Commit geladen und fuer
jede dort gelistete Datei die Git-Pruefsumme (SHA-1 des Git-Blobs) mit der
lokalen Datei verglichen. Nur geaenderte Dateien werden heruntergeladen.

Ablauf eines Updates (alles oder nichts):
  1. Alle geaenderten Dateien als "<ziel>.neu" herunterladen und pruefen:
     Git-Pruefsumme muss stimmen, .py-Dateien muessen compile()-faehig sein.
     Schlaegt eine Datei fehl, werden alle .neu-Dateien verworfen.
  2. Alle Dateien ersetzen, die alte Version bleibt als "<ziel>.bak" (wie
     beim Upload-Update in pico_tools/update.py).
  3. Bei .py-Dateien: "update_ausstehend.json" schreiben und neu starten.
     main.py ruft nach erfolgreichem Start bestaetigen() auf. Bleibt das
     aus, nimmt boot.py das Update zurueck (siehe boot.py).

Verwendung (main.py):

    from pico_tools import github_update

    github_update.einrichten("besitzer/repo", zweig="main", ordner="Picodesk",
                             darf_aktualisieren=lambda: wlan.modus == "normal")
    github_update.bestaetigen()            # nach erfolgreichem Start
    ...
    # in der Webserver-Schleife regelmaessig (blockiert nur, wenn faellig):
    github_update.tick()
    # im HTTP-Dispatcher:
    elif pfad.startswith("/github/"):
        github_update.anfrage_bearbeiten(client, pfad, http_antwort)

HTTP-Endpunkte:
  GET /github/status               -> Status als JSON
  GET /github/pruefen              -> sofort pruefen (auch einen zurueck-
                                      gerollten Stand erneut versuchen)
  GET /github/automatisch?an=0|1   -> automatische Pruefung aus/ein

Das Repository muss oeffentlich sein (anonyme GitHub-API, max. 60
Anfragen/Stunde pro IP - bei stuendlicher Pruefung meist nur eine).
"""

import gc
import os
import json
import time
import machine
import hashlib
import binascii

from pico_tools import update

# ---------------------------------------------------------------------------
# Konfiguration (per einrichten() setzen)
# ---------------------------------------------------------------------------

REPO = ""                    # "besitzer/repo"
ZWEIG = "main"
ORDNER = ""                  # Unterordner im Repo = Wurzel des Pico ("" = Repo-Wurzel)
MANIFEST = "pico_dateien.txt"
INTERVALL_SEK = 60 * 60      # wie oft automatisch geprueft wird
ERSTE_PRUEFUNG_SEK = 2 * 60  # erste Pruefung nach dem Start
FEHLER_WARTEN_SEK = 10 * 60  # naechster Versuch nach einem Fehler
DARF_AKTUALISIEREN = None    # optionale Funktion -> bool, z.B. "Tisch faehrt gerade nicht"

# Dateinamen muessen zu boot.py passen
EINSTELLUNGEN_DATEI = "github_update.json"
AUSSTEHEND_DATEI = "update_ausstehend.json"
ABGELEHNT_DATEI = "update_abgelehnt.txt"

_API = "https://api.github.com/repos/"
_RAW = "https://raw.githubusercontent.com/"
_MANIFEST_TEMP = "github_manifest.tmp"

# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------

automatisch = True
stand = None                 # installierter Commit (40 Zeichen) oder None
_verfuegbar = None           # zuletzt auf GitHub gesehener Commit
_naechste_pruefung = time.time() + ERSTE_PRUEFUNG_SEK
_letzte_pruefung = None
_sofort = False
_meldung = "Noch nicht geprueft"
_fehler = False


def _entfernen(pfad):
    try:
        os.remove(pfad)
    except OSError:
        pass


def _existiert(pfad):
    try:
        os.stat(pfad)
        return True
    except OSError:
        return False


def _laden():
    global automatisch, stand
    try:
        with open(EINSTELLUNGEN_DATEI) as f:
            daten = json.load(f)
        automatisch = bool(daten.get("automatisch", True))
        stand = daten.get("stand")
    except (OSError, ValueError):
        pass


def _speichern():
    with open(EINSTELLUNGEN_DATEI, "w") as f:
        json.dump({"automatisch": automatisch, "stand": stand}, f)


def _abgelehnt():
    try:
        with open(ABGELEHNT_DATEI) as f:
            return f.read().strip()
    except OSError:
        return None


def einrichten(repo, zweig="main", ordner="", darf_aktualisieren=None):
    """Setzt Repository/Zweig/Unterordner und laedt die gespeicherten
    Einstellungen (automatisch an/aus, installierter Stand)."""
    global REPO, ZWEIG, ORDNER, DARF_AKTUALISIEREN, _naechste_pruefung
    REPO = repo
    ZWEIG = zweig
    ORDNER = ordner.strip("/")
    DARF_AKTUALISIEREN = darf_aktualisieren
    _naechste_pruefung = time.time() + ERSTE_PRUEFUNG_SEK
    _laden()


def bestaetigen():
    """Nach einem erfolgreichen Start aufrufen: markiert ein gerade
    installiertes Update als gelungen, damit boot.py es nicht zuruecknimmt."""
    global stand, _meldung
    if not _existiert(AUSSTEHEND_DATEI):
        return
    try:
        with open(AUSSTEHEND_DATEI) as f:
            commit = json.load(f).get("commit")
    except (OSError, ValueError):
        commit = None
    _entfernen(AUSSTEHEND_DATEI)
    if commit:
        stand = commit
        _speichern()
        _entfernen(ABGELEHNT_DATEI)
        _meldung = "Update auf " + commit[:7] + " installiert"


# ---------------------------------------------------------------------------
# GitHub-Zugriff
# ---------------------------------------------------------------------------

def _anfrage(url, accept=None):
    try:
        import requests
    except ImportError:
        import urequests as requests
    kopf = {"User-Agent": "pico-github-update"}
    if accept:
        kopf["Accept"] = accept
    gc.collect()
    try:
        # Timeout, damit ein haengender Download den Webserver nicht blockiert
        antwort = requests.get(url, headers=kopf, timeout=20)
    except TypeError:
        antwort = requests.get(url, headers=kopf)  # aeltere urequests ohne timeout
    if antwort.status_code != 200:
        code = antwort.status_code
        antwort.close()
        if code == 403:
            raise OSError("GitHub-Anfragelimit erreicht (HTTP 403)")
        raise OSError("GitHub antwortet mit HTTP {}".format(code))
    return antwort


def _remote_commit():
    antwort = _anfrage(_API + REPO + "/commits/" + ZWEIG, "application/vnd.github.sha")
    try:
        sha = antwort.text.strip()
    finally:
        antwort.close()
    if len(sha) != 40:
        raise ValueError("Unerwartete Antwort von GitHub")
    return sha


def _baum(commit, unterordner=""):
    """Dateien (nicht rekursiv) eines Ordners im Commit als
    {pfad_auf_dem_pico: git_sha}."""
    pfad = "/".join(t for t in (ORDNER, unterordner) if t)
    url = _API + REPO + "/git/trees/" + commit + (":" + pfad if pfad else "")
    antwort = _anfrage(url)
    try:
        daten = antwort.json()
    finally:
        antwort.close()
    ergebnis = {}
    for eintrag in daten.get("tree", ()):
        if eintrag.get("type") == "blob":
            name = eintrag["path"]
            ergebnis[unterordner + "/" + name if unterordner else name] = eintrag["sha"]
    return ergebnis


def _herunterladen(commit, pfad, ziel):
    url = _RAW + REPO + "/" + commit + "/" + (ORDNER + "/" if ORDNER else "") + pfad
    antwort = _anfrage(url)
    groesse = 0
    try:
        with open(ziel, "wb") as f:
            while True:
                stueck = antwort.raw.read(1024)
                if not stueck:
                    break
                groesse += len(stueck)
                if groesse > update.MAX_BYTES:
                    raise ValueError(pfad + " ist zu gross")
                f.write(stueck)
    finally:
        antwort.close()


def _git_sha(pfad):
    """SHA-1 so, wie Git sie fuer eine Datei berechnet ("blob <laenge>\\0"
    + Inhalt) - direkt vergleichbar mit den Hashes aus der GitHub-API."""
    try:
        groesse = os.stat(pfad)[6]
    except OSError:
        return None
    h = hashlib.sha1()
    h.update("blob {}\0".format(groesse).encode())
    with open(pfad, "rb") as f:
        while True:
            stueck = f.read(512)
            if not stueck:
                break
            h.update(stueck)
    return binascii.hexlify(h.digest()).decode()


def _manifest_lesen(pfad):
    dateien = []
    with open(pfad) as f:
        for zeile in f:
            zeile = zeile.split("#", 1)[0].strip()
            if not zeile:
                continue
            if not update.ziel_gueltig(zeile):
                raise ValueError("Ungueltiger Eintrag im Manifest: " + zeile)
            dateien.append(zeile)
    return dateien


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def pruefen(manuell=False):
    """Prueft auf ein neues Update und installiert es. Gibt eine Meldung
    zurueck, wirft bei Fehlern eine Exception. Startet bei .py-Aenderungen
    neu (kehrt dann nicht zurueck)."""
    global stand, _verfuegbar

    commit = _remote_commit()
    _verfuegbar = commit
    if commit == stand:
        return "Aktuell"
    if not manuell and commit == _abgelehnt():
        return "Stand " + commit[:7] + " wurde zurueckgenommen - nur manuell erneut versuchen"

    remote = _baum(commit)
    if MANIFEST not in remote:
        raise ValueError(MANIFEST + " fehlt im Repository")
    _herunterladen(commit, MANIFEST, _MANIFEST_TEMP)
    try:
        dateien = _manifest_lesen(_MANIFEST_TEMP)
    finally:
        _entfernen(_MANIFEST_TEMP)

    for unterordner in set(p.rsplit("/", 1)[0] for p in dateien if "/" in p):
        remote.update(_baum(commit, unterordner))

    zu_laden = []
    for pfad in dateien:
        sha = remote.get(pfad)
        if sha is None:
            raise ValueError(pfad + " fehlt im Repository")
        if _git_sha(pfad) != sha:
            zu_laden.append((pfad, sha))
    del remote
    gc.collect()

    if not zu_laden:
        stand = commit
        _speichern()
        return "Aktuell"

    # 1. Alles herunterladen und pruefen, bevor irgendetwas ersetzt wird
    try:
        for pfad, sha in zu_laden:
            update.ordner_sicherstellen(pfad)
            neu = update.temp_dateiname(pfad)
            _herunterladen(commit, pfad, neu)
            if _git_sha(neu) != sha:
                raise ValueError(pfad + ": Pruefsumme stimmt nicht")
            gueltig, fehler = update.pruefen(neu, pfad.endswith(".py"))
            if not gueltig:
                raise ValueError(pfad + ": " + fehler)
    except Exception:
        for pfad, _ in zu_laden:
            update.aufraeumen(update.temp_dateiname(pfad))
        raise

    # 2. Ersetzen (alte Versionen bleiben als .bak)
    pfade = [pfad for pfad, _ in zu_laden]
    neustart = any(pfad.endswith(".py") for pfad in pfade)
    if neustart:
        with open(AUSSTEHEND_DATEI, "w") as f:
            json.dump({
                "commit": commit,
                "dateien": pfade,
                "neu": [pfad for pfad in pfade if not _existiert(pfad)],
                "starts": 0,
            }, f)
    for pfad in pfade:
        update.uebernehmen(pfad, update.temp_dateiname(pfad), update.backup_dateiname(pfad))

    if not neustart:
        stand = commit
        _speichern()
        return "{} Datei(en) aktualisiert".format(len(pfade))

    # 3. Neustart - main.py bestaetigt danach per bestaetigen()
    print("GitHub-Update auf", commit[:7], "installiert:", ", ".join(pfade), "- Neustart")
    time.sleep(0.5)
    machine.reset()


def tick():
    """Regelmaessig aus der Hauptschleife aufrufen. Kehrt sofort zurueck,
    solange keine Pruefung faellig ist."""
    global _sofort, _naechste_pruefung, _letzte_pruefung, _meldung, _fehler
    if not REPO:
        return
    jetzt = time.time()
    if not _sofort and not (automatisch and jetzt >= _naechste_pruefung):
        return
    if DARF_AKTUALISIEREN is not None and not DARF_AKTUALISIEREN():
        return  # spaeter erneut versuchen
    manuell = _sofort
    _sofort = False
    _letzte_pruefung = jetzt
    try:
        _meldung = pruefen(manuell)
        _fehler = False
    except Exception as exc:
        _meldung = "Fehler: " + str(exc)
        _fehler = True
        print("GitHub-Update:", _meldung)
    _naechste_pruefung = time.time() + (FEHLER_WARTEN_SEK if _fehler else INTERVALL_SEK)
    gc.collect()


def status():
    jetzt = time.time()
    return {
        "repo": REPO,
        "zweig": ZWEIG,
        "automatisch": automatisch,
        "stand": stand[:7] if stand else None,
        "verfuegbar": _verfuegbar[:7] if _verfuegbar else None,
        "laeuft": _sofort,
        "meldung": _meldung,
        "fehler": _fehler,
        "vor_sek": int(jetzt - _letzte_pruefung) if _letzte_pruefung is not None else None,
        "naechste_in_sek": max(0, int(_naechste_pruefung - jetzt)) if automatisch else None,
    }


def anfrage_bearbeiten(client, pfad, http_antwort):
    """Behandelt GET /github/status, /github/pruefen, /github/automatisch."""
    global _sofort, automatisch, _naechste_pruefung
    if pfad.startswith("/github/pruefen"):
        _sofort = True
    elif pfad.startswith("/github/automatisch"):
        an = "an=1" in pfad or "an=true" in pfad
        if an and not automatisch:
            _naechste_pruefung = time.time() + 5
        automatisch = an
        _speichern()
    elif not pfad.startswith("/github/status"):
        http_antwort(client, "404 Not Found", json.dumps({"ok": False}), "application/json")
        return
    http_antwort(client, "200 OK", json.dumps(status()), "application/json")
