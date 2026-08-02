"""HTTP-Client fuer die REST-API des Pico (siehe main.py auf dem Pico:
/info, /verlauf, /aktion/<name>, /start/<name>, /stop/<name>,
/automatik/..., /anwesenheit/..., /wlan/status, /wlan/speichern).

Nutzt nur die Python-Standardbibliothek (urllib), damit die App ohne
zusaetzliche native Abhaengigkeiten baubar bleibt. Alle Methoden sind
blockierend (einfache synchrone HTTP-Requests) - Aufrufer (siehe main.py
der App) muessen sie in einem Hintergrund-Thread ausfuehren, um die
Kivy-UI nicht einzufrieren.
"""

import json
import urllib.error
import urllib.request

ZEITLIMIT_SEK = 5


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
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PicoFehler(str(exc))

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

    def anwesenheit_start(self, abfrage_sek, timeout_min):
        return self._anfrage(
            "/anwesenheit/start?abfrage={}&timeout={}".format(abfrage_sek, timeout_min)
        )

    def anwesenheit_stop(self):
        return self._anfrage("/anwesenheit/stop")

    # --- WLAN-Einstellungen -----------------------------------------------
    def wlan_status(self):
        return self._anfrage("/wlan/status")

    def wlan_speichern(self, ssid, passwort):
        return self._anfrage("/wlan/speichern", methode="POST", daten={"ssid": ssid, "password": passwort})
