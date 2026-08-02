"""Pico Steuerung - Android-App (Kivy)

Spiegelt die Weboberflaeche des Pico (index.html/einstellungen.html) nativ:
Steuerung (Auf/Ab/Stehen/Sitzen), Automatik, Bewegungserkennung, Verlauf und
WLAN-Einstellungen. Findet den Pico automatisch per UDP-Discovery (siehe
discovery.py, Gegenstueck zum Discovery-Responder in main.py auf dem Pico) -
unabhaengig davon, ob er im normalen WLAN oder im eigenen Recovery-Hotspot
laeuft. Alle Netzwerkaufrufe laufen in Hintergrund-Threads, Ergebnisse
werden per @mainthread sicher an die UI zurueckgegeben.
"""

import os
import threading

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput

import wifi_android
from discovery import pico_suchen
from picoclient import PicoClient, PicoFehler

# --- Farben (an das Web-UI in index.html angelehnt) -------------------------
FARBE_BG = (0.043, 0.067, 0.125, 1)
FARBE_LEISTE = (0.09, 0.12, 0.2, 1)
FARBE_KARTE = (0.16, 0.19, 0.26, 1)
FARBE_RAND = (1, 1, 1, 0.12)
FARBE_AKZENT = (0.216, 0.741, 0.973, 1)
FARBE_AKZENT2 = (0.659, 0.333, 0.969, 1)
FARBE_GRUEN = (0.290, 0.871, 0.502, 1)
FARBE_ROT = (0.973, 0.443, 0.443, 1)
FARBE_TEXT = (0.886, 0.910, 0.941, 1)
FARBE_DIM = (0.580, 0.639, 0.722, 1)

AKTUALISIERUNG_SEK = 10

VERLAUF_LABEL = {
    "auf": "Auf", "ab": "Ab", "stehen": "Stehen", "sitzen": "Sitzen",
    "automatik_ein": "Automatik gestartet", "automatik_aus": "Automatik gestoppt",
}
VERLAUF_BADGE = {"automatik": "Automatik", "sensor": "Sensor"}


def format_zeit(sek):
    sek = max(0, int(sek))
    return "{:02d}:{:02d}".format(sek // 60, sek % 60)


def format_vor(sek):
    sek = max(0, int(sek))
    if sek < 60:
        return "vor {}s".format(sek)
    minuten = sek // 60
    if minuten < 60:
        return "vor {}m".format(minuten)
    return "vor {}h".format(minuten // 60)


# --- Wiederverwendbare Bausteine ---------------------------------------------

class Karte(BoxLayout):
    """Abgerundete 'Karten'-Flaeche wie im Web-UI."""

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=dp(14), spacing=dp(10), **kwargs)
        self.size_hint_y = None
        self.bind(minimum_height=self.setter("height"))
        with self.canvas.before:
            Color(*FARBE_KARTE)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(16)])
            Color(*FARBE_RAND)
            self._linie = Line(width=1)
        self.bind(pos=self._update, size=self._update)

    def _update(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._linie.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(16))


def titel_label(text):
    lbl = Label(text=text, color=FARBE_TEXT, bold=True, font_size="15sp",
                size_hint_y=None, height=dp(24), halign="left", valign="middle")
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


def dim_label(text=""):
    lbl = Label(text=text, color=FARBE_DIM, font_size="12.5sp",
                size_hint_y=None, height=dp(20), halign="left", valign="middle")
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


def hinweis_label(text):
    """Mehrzeiliger, sich selbst an den Textinhalt anpassender Hinweistext
    (Breite folgt dem Layout, Hoehe folgt dem umgebrochenen Text)."""
    lbl = Label(text=text, color=FARBE_DIM, font_size="12sp",
                halign="left", valign="top", size_hint_y=None)
    lbl.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
    lbl.bind(texture_size=lambda inst, size: setattr(inst, "height", size[1]))
    return lbl


class AktionsButton(Button):
    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(64))
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.color = FARBE_TEXT
        self.bold = True
        self.font_size = "15sp"
        with self.canvas.before:
            self._farbe = Color(0.15, 0.35, 0.45, 0.55)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._update, size=self._update, state=self._update_farbe)

    def _update(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _update_farbe(self, *_args):
        self._farbe.rgba = (0.4, 0.2, 0.55, 0.75) if self.state == "down" else (0.15, 0.35, 0.45, 0.55)


def eingabe_feld(text):
    return TextInput(
        text=text, multiline=False, input_filter="int",
        foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.08),
        cursor_color=FARBE_AKZENT, size_hint_y=None, height=dp(40),
    )


def feld_mit_label(beschriftung, feld):
    box = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None, height=dp(64))
    box.add_widget(dim_label(beschriftung))
    box.add_widget(feld)
    return box


# --- Steuerung-Bildschirm ----------------------------------------------------

class SteuerungScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sperre = False  # verhindert Server-Aufrufe beim Anzeigen von Poll-Daten

        wurzel = ScrollView()
        self.inhalt = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(16), size_hint_y=None)
        self.inhalt.bind(minimum_height=self.inhalt.setter("height"))

        self.status_label = dim_label("Verbinde...")
        self.inhalt.add_widget(self.status_label)
        self.inhalt.add_widget(self._buttons_karte())
        self.inhalt.add_widget(self._automatik_karte())
        self.inhalt.add_widget(self._bewegung_karte())
        self.inhalt.add_widget(self._verlauf_karte())

        wurzel.add_widget(self.inhalt)
        self.add_widget(wurzel)

    def status_setzen(self, text):
        self.status_label.text = text

    def _buttons_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("Steuerung"))
        gitter = GridLayout(cols=2, spacing=dp(10), size_hint_y=None, height=dp(140))

        auf_btn = AktionsButton(text=u"↑ Auf", height=dp(140))
        ab_btn = AktionsButton(text=u"↓ Ab", height=dp(140))
        stehen_btn = AktionsButton(text="Stehen", height=dp(140))
        sitzen_btn = AktionsButton(text="Sitzen", height=dp(140))

        app = App.get_running_app
        auf_btn.bind(on_press=lambda *_: app().halten_start("auf"),
                     on_release=lambda *_: app().halten_stop("auf"))
        ab_btn.bind(on_press=lambda *_: app().halten_start("ab"),
                    on_release=lambda *_: app().halten_stop("ab"))
        stehen_btn.bind(on_release=lambda *_: app().aktion_senden("stehen"))
        sitzen_btn.bind(on_release=lambda *_: app().aktion_senden("sitzen"))

        for btn in (auf_btn, ab_btn, stehen_btn, sitzen_btn):
            gitter.add_widget(btn)
        karte.add_widget(gitter)
        return karte

    def _automatik_karte(self):
        karte = Karte()
        kopf = BoxLayout(size_hint_y=None, height=dp(28))
        kopf.add_widget(titel_label("Automatik"))
        self.automatik_switch = Switch(active=False, size_hint_x=None, width=dp(60))
        self.automatik_switch.bind(active=self._automatik_geaendert)
        kopf.add_widget(self.automatik_switch)
        karte.add_widget(kopf)

        felder = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(10))
        self.sitzen_input = eingabe_feld("90")
        self.stehen_input = eingabe_feld("30")
        felder.add_widget(feld_mit_label("Sitzen (Min)", self.sitzen_input))
        felder.add_widget(feld_mit_label("Stehen (Min)", self.stehen_input))
        karte.add_widget(felder)

        self.automatik_status_label = dim_label("ausgeschaltet")
        karte.add_widget(self.automatik_status_label)
        return karte

    def _automatik_geaendert(self, _instance, wert):
        if self._sperre:
            return
        App.get_running_app().automatik_umschalten(
            wert, self.sitzen_input.text or "90", self.stehen_input.text or "30", "sitzen")

    def _bewegung_karte(self):
        karte = Karte()
        kopf = BoxLayout(size_hint_y=None, height=dp(28))
        kopf.add_widget(titel_label("Bewegungserkennung"))
        self.bewegung_switch = Switch(active=True, size_hint_x=None, width=dp(60))
        self.bewegung_switch.bind(active=self._bewegung_geaendert)
        kopf.add_widget(self.bewegung_switch)
        karte.add_widget(kopf)

        felder = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(10))
        self.abfrage_input = eingabe_feld("3")
        self.timeout_input = eingabe_feld("10")
        felder.add_widget(feld_mit_label("Abfrage (Sek.)", self.abfrage_input))
        felder.add_widget(feld_mit_label("Timeout (Min.)", self.timeout_input))
        karte.add_widget(felder)

        self.bewegung_status_label = dim_label("ausgeschaltet")
        karte.add_widget(self.bewegung_status_label)
        return karte

    def _bewegung_geaendert(self, _instance, wert):
        if self._sperre:
            return
        App.get_running_app().anwesenheit_umschalten(
            wert, self.abfrage_input.text or "3", self.timeout_input.text or "10")

    def _verlauf_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("Verlauf"))
        self.verlauf_liste = BoxLayout(orientation="vertical", spacing=dp(6),
                                        size_hint_y=None)
        self.verlauf_liste.bind(minimum_height=self.verlauf_liste.setter("height"))
        karte.add_widget(self.verlauf_liste)
        return karte

    def daten_anzeigen(self, info, verlauf, automatik, anwesenheit):
        ip = info.get("ip") or "?"
        self.status_setzen("Verbunden: {} (v{})".format(ip, info.get("version", "?")))

        self._sperre = True
        self.automatik_switch.active = bool(automatik.get("aktiv"))
        if automatik.get("aktiv"):
            phase = "Sitzen" if automatik.get("phase") == "sitzen" else "Stehen"
            self.automatik_status_label.text = "Aktiv - {} - noch {}".format(
                phase, format_zeit(automatik.get("rest_sek", 0)))
            if automatik.get("sitzen_min"):
                self.sitzen_input.text = str(automatik["sitzen_min"])
            if automatik.get("stehen_min"):
                self.stehen_input.text = str(automatik["stehen_min"])
        else:
            self.automatik_status_label.text = "ausgeschaltet"

        self.bewegung_switch.active = bool(anwesenheit.get("aktiv"))
        if anwesenheit.get("aktiv"):
            distanz = anwesenheit.get("distanz_cm")
            self.bewegung_status_label.text = "{} cm - noch {} bis Automatik-Stopp".format(
                distanz if distanz is not None else "--", format_zeit(anwesenheit.get("rest_sek", 0)))
            if anwesenheit.get("abfrage_sek"):
                self.abfrage_input.text = str(int(anwesenheit["abfrage_sek"]))
            if anwesenheit.get("keine_aenderung_min"):
                self.timeout_input.text = str(int(anwesenheit["keine_aenderung_min"]))
        else:
            self.bewegung_status_label.text = "ausgeschaltet"
        self._sperre = False

        self.verlauf_liste.clear_widgets()
        if not verlauf:
            self.verlauf_liste.add_widget(dim_label("Noch keine Aktionen"))
        for eintrag in verlauf:
            zeile = BoxLayout(size_hint_y=None, height=dp(22))
            text = VERLAUF_LABEL.get(eintrag["aktion"], eintrag["aktion"])
            badge = VERLAUF_BADGE.get(eintrag.get("quelle"))
            if badge:
                text += "  [{}]".format(badge)
            eintrag_label = Label(text=text, color=FARBE_TEXT, font_size="13sp",
                                   halign="left", valign="middle")
            eintrag_label.bind(size=eintrag_label.setter("text_size"))
            zeit_label = Label(text=format_vor(eintrag.get("vor_sek", 0)), color=FARBE_DIM,
                                font_size="11.5sp", size_hint_x=None, width=dp(56))
            zeile.add_widget(eintrag_label)
            zeile.add_widget(zeit_label)
            self.verlauf_liste.add_widget(zeile)


# --- Einstellungen-Bildschirm ------------------------------------------------

class EinstellungenScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        wurzel = ScrollView()
        self.inhalt = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(16), size_hint_y=None)
        self.inhalt.bind(minimum_height=self.inhalt.setter("height"))

        self.inhalt.add_widget(self._verbindung_karte())
        self.inhalt.add_widget(self._wlan_karte())
        if wifi_android.IST_ANDROID:
            self.inhalt.add_widget(self._hotspot_karte())

        wurzel.add_widget(self.inhalt)
        self.add_widget(wurzel)

    def _verbindung_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("Verbindung zum Pico"))
        self.verbindung_status = dim_label("Unbekannt")
        karte.add_widget(self.verbindung_status)

        self.ip_input = TextInput(
            text="", hint_text="IP-Adresse manuell eingeben", multiline=False,
            foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.08),
            cursor_color=FARBE_AKZENT, size_hint_y=None, height=dp(40),
        )
        karte.add_widget(self.ip_input)

        reihe = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        suchen_btn = AktionsButton(text="Automatisch suchen", height=dp(48))
        suchen_btn.bind(on_release=lambda *_: App.get_running_app().jetzt_suchen())
        verbinden_btn = AktionsButton(text="Verbinden", height=dp(48))
        verbinden_btn.bind(
            on_release=lambda *_: App.get_running_app().manuell_verbinden(self.ip_input.text.strip()))
        reihe.add_widget(suchen_btn)
        reihe.add_widget(verbinden_btn)
        karte.add_widget(reihe)
        return karte

    def _wlan_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("WLAN des Pico"))
        self.wlan_status_label = dim_label("Unbekannt")
        karte.add_widget(self.wlan_status_label)

        self.ssid_input = TextInput(
            text="", hint_text="WLAN-Name (SSID)", multiline=False,
            foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.08),
            cursor_color=FARBE_AKZENT, size_hint_y=None, height=dp(40),
        )
        self.passwort_input = TextInput(
            text="", hint_text="WLAN-Passwort", password=True, multiline=False,
            foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.08),
            cursor_color=FARBE_AKZENT, size_hint_y=None, height=dp(40),
        )
        karte.add_widget(self.ssid_input)
        karte.add_widget(self.passwort_input)

        speichern_btn = AktionsButton(text="Speichern & Pico neu starten", height=dp(48))
        speichern_btn.bind(on_release=self._wlan_speichern)
        karte.add_widget(speichern_btn)

        self.wlan_speichern_status = dim_label("")
        karte.add_widget(self.wlan_speichern_status)
        return karte

    def _hotspot_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("Pico-Hotspot (Android)"))
        hinweis = hinweis_label(
            'Findet der Pico kein WLAN, oeffnet er selbst einen Hotspot ("{}"). '
            '"Vorschlagen" registriert ihn bei Android, das sich dann automatisch '
            'verbindet, sobald er in Reichweite ist (einmalige Freigabe unter '
            'Einstellungen -> WLAN -> Netzwerkvorschlaege noetig).'.format(
                wifi_android.AP_SSID_STANDARD)
        )
        karte.add_widget(hinweis)

        reihe = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        vorschlagen_btn = AktionsButton(text="Hotspot vorschlagen", height=dp(48))
        vorschlagen_btn.bind(on_release=self._hotspot_vorschlagen)
        oeffnen_btn = AktionsButton(text="WLAN-Einstellungen", height=dp(48))
        oeffnen_btn.bind(on_release=lambda *_: wifi_android.wifi_einstellungen_oeffnen())
        reihe.add_widget(vorschlagen_btn)
        reihe.add_widget(oeffnen_btn)
        karte.add_widget(reihe)

        self.hotspot_status_label = dim_label("")
        karte.add_widget(self.hotspot_status_label)
        return karte

    def _hotspot_vorschlagen(self, *_args):
        erfolg, meldung = wifi_android.hotspot_vorschlagen()
        self.hotspot_status_label.text = meldung
        self.hotspot_status_label.color = FARBE_GRUEN if erfolg else FARBE_ROT

    def _wlan_speichern(self, *_args):
        app = App.get_running_app()
        if not app.client:
            self.wlan_speichern_status.text = "Nicht mit dem Pico verbunden"
            self.wlan_speichern_status.color = FARBE_ROT
            return
        ssid = self.ssid_input.text.strip()
        if not ssid:
            self.wlan_speichern_status.text = "SSID darf nicht leer sein"
            self.wlan_speichern_status.color = FARBE_ROT
            return

        self.wlan_speichern_status.text = "Speichere..."
        self.wlan_speichern_status.color = FARBE_DIM

        def callback(erfolg, fehler):
            if erfolg:
                self.wlan_speichern_status.text = "Gespeichert - Pico startet neu"
                self.wlan_speichern_status.color = FARBE_GRUEN
            else:
                self.wlan_speichern_status.text = "Fehler: " + fehler
                self.wlan_speichern_status.color = FARBE_ROT

        app.wlan_speichern(ssid, self.passwort_input.text, callback)

    def aktualisieren(self):
        app = App.get_running_app()
        if app.verbunden:
            self.verbindung_status.text = "Verbunden"
            self.verbindung_status.color = FARBE_GRUEN
        else:
            self.verbindung_status.text = "Nicht verbunden"
            self.verbindung_status.color = FARBE_ROT

        if not app.client:
            return

        client = app.client

        def arbeit():
            try:
                status = client.wlan_status()
            except PicoFehler:
                return
            self._wlan_status_anzeigen(status)

        threading.Thread(target=arbeit, daemon=True).start()

    @mainthread
    def _wlan_status_anzeigen(self, status):
        modus = status.get("modus")
        ssid = status.get("ssid_konfiguriert", "?")
        ip = status.get("ip", "?")
        if modus == "hotspot":
            self.wlan_status_label.text = 'Hotspot aktiv ("{}") - Verbindung zu "{}" fehlgeschlagen'.format(
                status.get("hotspot_ssid", "?"), ssid)
        else:
            self.wlan_status_label.text = 'Verbunden mit "{}" ({})'.format(ssid, ip)
        if not self.ssid_input.text:
            self.ssid_input.text = ssid


# --- App ----------------------------------------------------------------------

class PicoSteuerungApp(App):
    def build(self):
        self.title = "Pico Steuerung"
        self.client = None
        self.verbunden = False
        self.speicher = JsonStore(os.path.join(self.user_data_dir, "pico_app.json"))

        Window.clearcolor = FARBE_BG
        wifi_android.berechtigungen_anfordern()

        self.sm = ScreenManager(transition=NoTransition())
        self.steuerung_screen = SteuerungScreen(name="steuerung")
        self.einstellungen_screen = EinstellungenScreen(name="einstellungen")
        self.sm.add_widget(self.steuerung_screen)
        self.sm.add_widget(self.einstellungen_screen)

        wurzel = BoxLayout(orientation="vertical")
        wurzel.add_widget(self._kopfleiste())
        wurzel.add_widget(self.sm)

        self._verbindung_wiederherstellen()
        Clock.schedule_interval(self._aktualisieren_tick, AKTUALISIERUNG_SEK)

        return wurzel

    def _kopfleiste(self):
        leiste = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(14), 0), spacing=dp(8))
        with leiste.canvas.before:
            Color(*FARBE_LEISTE)
            self._leiste_rect = RoundedRectangle(pos=leiste.pos, size=leiste.size, radius=[0])
        leiste.bind(pos=self._update_leiste, size=self._update_leiste)

        titel = Label(text="Pico Steuerung", color=FARBE_TEXT, bold=True, font_size="17sp",
                      halign="left", valign="middle")
        titel.bind(size=titel.setter("text_size"))
        leiste.add_widget(titel)

        self.verbindungspunkt = Label(text=u"●", color=FARBE_DIM, size_hint_x=None, width=dp(22))
        leiste.add_widget(self.verbindungspunkt)

        einstellungen_btn = Button(
            text=u"⚙", size_hint_x=None, width=dp(40),
            background_normal="", background_color=(0, 0, 0, 0), color=FARBE_TEXT, font_size="20sp",
        )
        einstellungen_btn.bind(on_release=self._bildschirm_umschalten)
        leiste.add_widget(einstellungen_btn)
        return leiste

    def _update_leiste(self, instance, *_args):
        self._leiste_rect.pos = instance.pos
        self._leiste_rect.size = instance.size

    def _bildschirm_umschalten(self, *_args):
        self.sm.current = "einstellungen" if self.sm.current == "steuerung" else "steuerung"
        if self.sm.current == "einstellungen":
            self.einstellungen_screen.aktualisieren()

    # --- Verbindung -----------------------------------------------------------

    def _verbindung_wiederherstellen(self):
        letzte_ip = None
        if self.speicher.exists("pico"):
            letzte_ip = self.speicher.get("pico").get("ip")

        def arbeit():
            if letzte_ip:
                client = PicoClient("http://{}".format(letzte_ip))
                try:
                    client.info()
                    self._verbindung_setzen(letzte_ip)
                    return
                except PicoFehler:
                    pass
            self._discovery_versuchen()

        threading.Thread(target=arbeit, daemon=True).start()

    def _discovery_versuchen(self):
        gefunden = pico_suchen()
        if gefunden:
            self._verbindung_setzen(gefunden[0]["ip"])
        else:
            self._verbindung_verloren()

    @mainthread
    def _verbindung_setzen(self, ip):
        self.client = PicoClient("http://{}".format(ip))
        self.verbunden = True
        self.verbindungspunkt.color = FARBE_GRUEN
        self.speicher.put("pico", ip=ip)
        self.steuerung_screen.status_setzen("Verbunden: " + ip)
        if self.sm.current == "einstellungen":
            self.einstellungen_screen.aktualisieren()

    @mainthread
    def _verbindung_verloren(self):
        self.verbunden = False
        self.verbindungspunkt.color = FARBE_ROT
        self.steuerung_screen.status_setzen("Pico nicht gefunden - suche weiter...")
        if self.sm.current == "einstellungen":
            self.einstellungen_screen.aktualisieren()

    def jetzt_suchen(self):
        threading.Thread(target=self._discovery_versuchen, daemon=True).start()

    def manuell_verbinden(self, ip):
        if not ip:
            return

        def arbeit():
            client = PicoClient("http://{}".format(ip))
            try:
                client.info()
                self._verbindung_setzen(ip)
            except PicoFehler:
                self._verbindung_verloren()

        threading.Thread(target=arbeit, daemon=True).start()

    # --- Periodisches Update (alle 10s, siehe AKTUALISIERUNG_SEK) -------------

    def _aktualisieren_tick(self, _dt):
        threading.Thread(target=self._aktualisieren_arbeit, daemon=True).start()

    def _aktualisieren_arbeit(self):
        if self.client is None:
            self._discovery_versuchen()
            return
        try:
            info = self.client.info()
            verlauf = self.client.verlauf()
            automatik = self.client.automatik_status()
            anwesenheit = self.client.anwesenheit_status()
        except PicoFehler:
            self.client = None
            self._verbindung_verloren()
            return
        self._daten_anzeigen(info, verlauf, automatik, anwesenheit)

    @mainthread
    def _daten_anzeigen(self, info, verlauf, automatik, anwesenheit):
        self.verbunden = True
        self.verbindungspunkt.color = FARBE_GRUEN
        self.steuerung_screen.daten_anzeigen(info, verlauf, automatik, anwesenheit)

    # --- Aktionen ---------------------------------------------------------------

    def _sicher(self, funktion, *args):
        try:
            funktion(*args)
        except PicoFehler:
            self.client = None
            self._verbindung_verloren()

    def aktion_senden(self, name):
        if not self.client:
            return
        threading.Thread(target=lambda: self._sicher(self.client.aktion, name), daemon=True).start()

    def halten_start(self, name):
        if not self.client:
            return
        threading.Thread(target=lambda: self._sicher(self.client.start, name), daemon=True).start()

    def halten_stop(self, name):
        if not self.client:
            return
        threading.Thread(target=lambda: self._sicher(self.client.stop, name), daemon=True).start()

    def automatik_umschalten(self, aktiv, sitzen_min, stehen_min, phase):
        if not self.client:
            return

        def arbeit():
            try:
                if aktiv:
                    self.client.automatik_start(sitzen_min, stehen_min, phase)
                else:
                    self.client.automatik_stop()
            except PicoFehler:
                pass

        threading.Thread(target=arbeit, daemon=True).start()

    def anwesenheit_umschalten(self, aktiv, abfrage_sek, timeout_min):
        if not self.client:
            return

        def arbeit():
            try:
                if aktiv:
                    self.client.anwesenheit_start(abfrage_sek, timeout_min)
                else:
                    self.client.anwesenheit_stop()
            except PicoFehler:
                pass

        threading.Thread(target=arbeit, daemon=True).start()

    def wlan_speichern(self, ssid, passwort, callback):
        client = self.client

        def arbeit():
            try:
                client.wlan_speichern(ssid, passwort)
                Clock.schedule_once(lambda _dt: callback(True, ""))
            except PicoFehler as exc:
                fehler = str(exc)
                Clock.schedule_once(lambda _dt: callback(False, fehler))

        threading.Thread(target=arbeit, daemon=True).start()


if __name__ == "__main__":
    PicoSteuerungApp().run()
