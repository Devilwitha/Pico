"""HTTP-Client fuer die REST-API des Pico (siehe main.py auf dem Pico:
/info, /verlauf, /aktion/<name>, /start/<name>, /stop/<name>,
/automatik/..., /anwesenheit/..., /wlan/status, /wlan/speichern,
/name/speichern, /dateien/..., /update).

Nutzt nur die Python-Standardbibliothek (urllib), damit die App ohne
zusaetzliche native Abhaengigkeiten baubar bleibt. Alle Methoden sind
blockierend (einfache synchrone HTTP-Requests) - Aufrufer (siehe main.py
der App) muessen sie in einem Hintergrund-Thread ausfuehren, um die
Kivy-UI nicht einzufrieren.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

ZEITLIMIT_SEK = 5
# Update-/Datei-Uploads brauchen laenger als normale Anfragen (main.py wird
# auf dem Pico per compile() geprueft, bevor die Antwort zurueckkommt)
UPDATE_ZEITLIMIT_SEK = 25


class PicoFehler(Exception):
    """Wird bei jedem Netzwerk- oder Antwortfehler geworfen, damit
    Aufrufer nicht zwischen urllib-, OS- und JSON-Fehlern unterscheiden
    muessen."""


class PicoClient:
    def __init__(self, basis_url):
        self.basis_url = basis_url.rstrip("/")

    def _anfrage(self, pfad, methode="GET", daten=None):
        body = json.dumps(daten).encode("utf-8") if daten is not None else None
        request = urllib.request.Request(self.basis_url + pfad, data=body, method=methode)
        try:
            with urllib.request.urlopen(request, timeout=ZEITLIMIT_SEK) as antwort:
                return json.loads(antwort.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PicoFehler(self._fehlertext_aus_antwort(exc) or str(exc))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PicoFehler(str(exc))

    @staticmethod
    def _fehlertext_aus_antwort(exc):
        """Der Pico liefert bei 400/404-Antworten meist ein JSON-Objekt mit
        einem 'fehler'-Feld (z.B. "main.py kann nicht geloescht werden") -
        das ist deutlich hilfreicher als der generische HTTPError-Text."""
        try:
            daten = json.loads(exc.read().decode("utf-8"))
            return daten.get("fehler")
        except (ValueError, OSError, AttributeError):
            return None

    # --- Geraet -------------------------------------------------------------
    def info(self):
        return self._anfrage("/info")

    def verlauf(self):
        return self._anfrage("/verlauf")

    def neustart(self):
        return self._anfrage("/neustart")

    # --- Aktionen -------------------------------------------------------------
    def aktion(self, name):
        return self._anfrage("/aktion/" + name)

    def start(self, name):
        return self._anfrage("/start/" + name)

    def stop(self, name):
        return self._anfrage("/stop/" + name)

    # --- Automatik ------------------------------------------------------------
    def automatik_status(self):
        return self._anfrage("/automatik/status")

    def automatik_start(self, sitzen_min, stehen_min, phase="sitzen"):
        return self._anfrage(
            "/automatik/start?sitzen={}&stehen={}&phase={}".format(sitzen_min, stehen_min, phase)
        )

    def automatik_stop(self):
        return self._anfrage("/automatik/stop")

    # --- Bewegungserkennung -----------------------------------------------
    def anwesenheit_status(self):
        return self._anfrage("/anwesenheit/status")

    def anwesenheit_start(self, abfrage_sek, timeout_min, schwellwert_cm):
        return self._anfrage(
            "/anwesenheit/start?abfrage={}&timeout={}&schwellwert={}".format(
                abfrage_sek, timeout_min, schwellwert_cm)
        )

    def anwesenheit_stop(self):
        return self._anfrage("/anwesenheit/stop")

    # --- WLAN-Einstellungen -----------------------------------------------
    def wlan_status(self):
        return self._anfrage("/wlan/status")

    def wlan_speichern(self, ssid, passwort):
        return self._anfrage("/wlan/speichern", methode="POST", daten={"ssid": ssid, "password": passwort})

    # --- Geraetename ------------------------------------------------------
    def name_speichern(self, name):
        return self._anfrage("/name/speichern", methode="POST", daten={"name": name})

    # --- Dateiverwaltung ----------------------------------------------------
    # (Hochladen/Ueberschreiben bzw. Neuanlegen laeuft ueber update_hochladen,
    # denselben Mechanismus wie ein main.py/index.html-Update - siehe unten.)
    def dateien_liste(self):
        return self._anfrage("/dateien/liste")

    def datei_lesen(self, name):
        """Liest eine Datei als Text - anders als die uebrigen Endpunkte
        liefert /dateien/lesen bei Erfolg keinen JSON-, sondern reinen
        Text-Body, daher hier ohne _anfrage()."""
        url = self.basis_url + "/dateien/lesen?name=" + urllib.parse.quote(name)
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=ZEITLIMIT_SEK) as antwort:
                return antwort.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise PicoFehler(self._fehlertext_aus_antwort(exc) or str(exc))
        except (urllib.error.URLError, OSError) as exc:
            raise PicoFehler(str(exc))

    def datei_loeschen(self, name):
        return self._anfrage("/dateien/loeschen?name=" + urllib.parse.quote(name))

    # --- Update: Datei unter ihrem eigenen Namen hochladen -----------------
    # (identischer Mechanismus wie der Datei-Upload im Web-UI: existiert die
    # Datei schon auf dem Pico, wird nur sie ersetzt (Backup als "<name>.bak"),
    # .py-Dateien werden vor der Uebernahme geprueft und starten den Pico neu)
    def update_hochladen(self, ziel, inhalt_bytes):
        """Gibt bei Erfolg UND bei einer (vom Pico abgelehnten) 400-Antwort
        das geparste JSON-Objekt zurueck ({'ok': bool, 'fehler': ...}) statt
        eine Exception zu werfen - der Aufrufer prueft 'ok' selbst, genau wie
        im Web-UI/der Windows-App. Nur bei echten Verbindungsfehlern wird
        PicoFehler geworfen."""
        url = self.basis_url + "/update?ziel=" + urllib.parse.quote(ziel)
        request = urllib.request.Request(url, data=inhalt_bytes, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=UPDATE_ZEITLIMIT_SEK) as antwort:
                return json.loads(antwort.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except (ValueError, OSError):
                raise PicoFehler(str(exc))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PicoFehler(str(exc))
