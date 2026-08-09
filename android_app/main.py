"""Pico Steuerung - Android-App (Kivy)

Spiegelt die Weboberflaeche des Pico (index.html/einstellungen.html/
dateien.html) nativ: Steuerung (Auf/Ab/Stehen/Sitzen, Position setzen),
Automatik, Bewegungserkennung, Verlauf, Geraetename, WLAN-Einstellungen,
Dateiverwaltung und Update-Upload. Findet den Pico automatisch per
UDP-Discovery (siehe discovery.py, Gegenstueck zum Discovery-Responder in
main.py auf dem Pico) - unabhaengig davon, ob er im normalen WLAN oder im
eigenen Recovery-Hotspot laeuft. Alle Netzwerkaufrufe laufen in
Hintergrund-Threads, Ergebnisse werden per @mainthread sicher an die UI
zurueckgegeben.
"""

import os
import threading

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle, Triangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

import wifi_android
from discovery import pico_suchen
from picoclient import PicoClient, PicoFehler

# --- Farben (an das Web-UI in index.html angelehnt) -------------------------
FARBE_BG = (0.043, 0.067, 0.125, 1)
FARBE_LEISTE = (0.09, 0.12, 0.2, 1)
FARBE_KARTE = (1, 1, 1, 0.055)
FARBE_RAND = (1, 1, 1, 0.12)
FARBE_AKZENT = (0.216, 0.741, 0.973, 1)
FARBE_AKZENT2 = (0.659, 0.333, 0.969, 1)
FARBE_GRUEN = (0.290, 0.871, 0.502, 1)
FARBE_ROT = (0.973, 0.443, 0.443, 1)
FARBE_TEXT = (0.886, 0.910, 0.941, 1)
FARBE_DIM = (0.580, 0.639, 0.722, 1)

AKTUALISIERUNG_SEK = 10
# Die Entfernungsmessung wird separat jede Sekunde abgefragt (wie im Web-UI),
# damit die cm-Anzeige/der Timeout-Timer live mitlaeuft statt erst alle 10s.
ANWESENHEIT_TICK_SEK = 1

VERLAUF_LABEL = {
    "auf": "Auf", "ab": "Ab", "stehen": "Stehen", "sitzen": "Sitzen",
    "stehen_setzen": "Stehposition gesetzt", "sitzen_setzen": "Sitzposition gesetzt",
    "automatik_ein": "Automatik gestartet", "automatik_aus": "Automatik gestoppt",
}
VERLAUF_BADGE = {"automatik": "Automatik", "sensor": "Sensor"}
# Nur Zeichen aus dem "Geometric Shapes"-Block bzw. reines ASCII: Emojis und
# viele Symbol-Codepoints (z.B. das Zahnrad-Icon) fehlen in der auf Android
# per Kivy gerenderten Schriftart oft und werden als leeres Kaestchen
# dargestellt - das war die Ursache der fehlerhaften Darstellung.
VERLAUF_ICON = {
    "auf": u"▲", "ab": u"▼", "stehen": "St", "sitzen": "Si",
    "stehen_setzen": "St*", "sitzen_setzen": "Si*",
    "automatik_ein": u"▶", "automatik_aus": u"■",
}


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


def format_groesse(bytes_):
    if bytes_ < 1024:
        return "{} B".format(bytes_)
    return "{:.1f} KB".format(bytes_ / 1024)


# Dateien, die sich ueber die Dateiverwaltung nicht loeschen lassen (siehe
# DATEIEN_GESCHUETZT in main.py auf dem Pico)
DATEIEN_GESCHUETZT = ("main.py", "boot.py")


def anwesenheit_phase_text(anwesenheit):
    """distanz_cm ist immer der letzte GUELTIGE Messwert - der Pico haelt ihn
    bei 'kein Echo' bewusst, damit die Anzeige nicht haengt. 'anwesend'
    zeigt an, ob genau jetzt eine Aenderung >= Schwellwert erkannt wurde."""
    distanz = anwesenheit.get("distanz_cm")
    if distanz is None:
        return "--"
    if anwesenheit.get("anwesend"):
        return "{} cm - erkannt".format(distanz)
    return "{} cm".format(distanz)


# --- Wiederverwendbare Bausteine ---------------------------------------------

class Karte(BoxLayout):
    """Abgerundete, leicht durchscheinende 'Karten'-Flaeche wie im Web-UI."""

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


class PfeilIcon(Widget):
    """Auf-/Ab-Pfeil als Vektor-Grafik statt Unicode-Zeichen - so ist die
    Darstellung auf jedem Geraet/jeder Schriftart garantiert gleich."""

    def __init__(self, richtung="auf", **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(18), dp(18)))
        super().__init__(**kwargs)
        self._richtung = richtung
        with self.canvas:
            Color(*FARBE_TEXT)
            self._dreieck = Triangle()
        self.bind(pos=self._update, size=self._update)

    def _update(self, *_args):
        x, y = self.pos
        w, h = self.size
        if self._richtung == "auf":
            self._dreieck.points = [x, y, x + w, y, x + w / 2, y + h]
        else:
            self._dreieck.points = [x, y + h, x + w, y + h, x + w / 2, y]


class PersonIcon(Widget):
    """Einfaches Strichmaennchen (stehend/sitzend) als Vektor-Grafik -
    vermeidet Emoji-Zeichen, die auf vielen Geraeten als leeres Kaestchen
    dargestellt werden."""

    def __init__(self, sitzend=False, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(18), dp(22)))
        super().__init__(**kwargs)
        self._sitzend = sitzend
        with self.canvas:
            Color(*FARBE_TEXT)
            self._kopf = Line(width=dp(1.6))
            self._koerper = Line(width=dp(1.8), cap="round", joint="round")
        self.bind(pos=self._update, size=self._update)

    def _update(self, *_args):
        x, y = self.pos
        w, h = self.size
        kopf_d = w * 0.55
        kopf_cx = x + w / 2
        kopf_cy = y + h - kopf_d / 2
        self._kopf.circle = (kopf_cx, kopf_cy, kopf_d / 2)
        if self._sitzend:
            sitzhoehe = y + h * 0.3
            self._koerper.points = [
                kopf_cx, kopf_cy - kopf_d / 2,
                kopf_cx, sitzhoehe,
                x + w, sitzhoehe,
            ]
        else:
            self._koerper.points = [kopf_cx, kopf_cy - kopf_d / 2, kopf_cx, y]


class MenuIcon(Widget):
    """Drei-Balken-Icon fuer den Einstellungen-Knopf, ebenfalls als Vektor-
    Grafik statt Zahnrad-Unicode-Zeichen (siehe PfeilIcon)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(20), dp(14)))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*FARBE_TEXT)
            self._balken = [Line(width=dp(1.6)) for _ in range(3)]
        self.bind(pos=self._update, size=self._update)

    def _update(self, *_args):
        x, y = self.pos
        w, h = self.size
        for i, linie in enumerate(self._balken):
            yy = y + h - i * (h / 2)
            linie.points = [x, yy, x + w, yy]


class OrdnerIcon(Widget):
    """Einfaches Ordner-Symbol als Vektor-Grafik fuer den Dateien-Knopf in
    der Kopfleiste (siehe PfeilIcon/MenuIcon - keine Emojis/Sonderzeichen,
    die auf manchen Geraeten/Schriftarten als leeres Kaestchen erscheinen)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(20), dp(16)))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*FARBE_TEXT)
            self._koerper = Line(width=dp(1.6), joint="miter")
            self._lasche = Line(width=dp(1.6), joint="miter")
        self.bind(pos=self._update, size=self._update)

    def _update(self, *_args):
        x, y = self.pos
        w, h = self.size
        lasche_h = h * 0.28
        lasche_w = w * 0.5
        self._lasche.points = [x, y + h, x, y + h - lasche_h, x + lasche_w * 0.6, y + h - lasche_h, x + lasche_w, y + h]
        self._koerper.points = [
            x, y + h - lasche_h, x, y, x + w, y, x + w, y + h - lasche_h * 0.3, x + lasche_w, y + h - lasche_h * 0.3,
        ]


class IconKnopf(ButtonBehavior, AnchorLayout):
    """Klickbare Flaeche fuer ein einzelnes Icon (z.B. Einstellungen-Knopf
    in der Kopfleiste)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_x", None)
        kwargs.setdefault("width", dp(40))
        super().__init__(**kwargs)
        self.bind(state=lambda inst, wert: setattr(inst, "opacity", 0.55 if wert == "down" else 1))


class Schalter(ButtonBehavior, Widget):
    """Runder, moderner Pill-Toggle mit gleitendem Knopf, wie '.schalter' im
    Web-UI - ersetzt den nativen Kivy-Switch (der optisch nicht zum Rest der
    App passt). 'active' verhaelt sich wie beim Switch: lesbar/schreibbar
    und bindbar (.bind(active=...)), damit der Rest des Codes unveraendert
    bleibt."""

    active = BooleanProperty(False)

    _AUS_FARBE = (1, 1, 1, 0.15)
    _AN_FARBE = (0.44, 0.54, 0.97, 1)
    _KNOPF_D = dp(20)
    _RAND = dp(3)

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(46), dp(26)))
        kwargs.setdefault("always_release", True)
        super().__init__(**kwargs)
        with self.canvas:
            self._spur_farbe = Color(*self._AN_FARBE if self.active else self._AUS_FARBE)
            self._spur = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.height / 2])
            Color(1, 1, 1, 1)
            self._knopf = Ellipse(pos=self.pos, size=(self._KNOPF_D, self._KNOPF_D))
        self.bind(pos=self._update, size=self._update, active=self._aktiv_geaendert)
        self._update()

    def _knopf_pos(self):
        y = self.y + (self.height - self._KNOPF_D) / 2
        if self.active:
            return (self.x + self.width - self._KNOPF_D - self._RAND, y)
        return (self.x + self._RAND, y)

    def _update(self, *_args):
        self._spur.pos = self.pos
        self._spur.size = self.size
        self._spur.radius = [self.height / 2]
        self._knopf.pos = self._knopf_pos()
        self._knopf.size = (self._KNOPF_D, self._KNOPF_D)

    def _aktiv_geaendert(self, *_args):
        ziel_farbe = self._AN_FARBE if self.active else self._AUS_FARBE
        Animation.cancel_all(self._spur_farbe)
        Animation.cancel_all(self._knopf)
        Animation(rgba=ziel_farbe, duration=0.18).start(self._spur_farbe)
        Animation(pos=self._knopf_pos(), duration=0.18, t="out_quad").start(self._knopf)

    def on_release(self):
        self.active = not self.active


class AktionsButton(ButtonBehavior, BoxLayout):
    """Abgerundete Aktions-Kachel (optional mit Icon ueber der Beschriftung),
    farblich an die Buttons im Web-UI angelehnt."""

    def __init__(self, text="", icon_widget=None, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(56))
        super().__init__(orientation="vertical", spacing=dp(4), **kwargs)
        with self.canvas.before:
            self._farbe = Color(0.31, 0.45, 0.55, 0.28)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
            Color(*FARBE_RAND)
            self._rand = Line(width=1)
        self.bind(pos=self._update, size=self._update, state=self._zustand_geaendert)

        if icon_widget is not None:
            icon_huelle = AnchorLayout(size_hint_y=None, height=dp(26))
            icon_huelle.add_widget(icon_widget)
            self.add_widget(icon_huelle)
        self.text_label = Label(text=text, bold=True, font_size="14.5sp", color=FARBE_TEXT)
        self.add_widget(self.text_label)

    def _update(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rand.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(14))

    def _zustand_geaendert(self, *_args):
        ziel = (0.659, 0.333, 0.969, 0.55) if self.state == "down" else (0.31, 0.45, 0.55, 0.28)
        Animation.cancel_all(self._farbe)
        Animation(rgba=ziel, duration=0.12).start(self._farbe)


class StatusBox(BoxLayout):
    """Rundes Status-Feld mit Phase/Timer/Sub-Text, wie '.automatik-status'
    im Web-UI (grosse Zeitanzeige statt einer einzelnen Textzeile)."""

    def __init__(self, phase_text="", **kwargs):
        super().__init__(orientation="vertical", spacing=dp(2), padding=(dp(12), dp(12)), **kwargs)
        self.size_hint_y = None
        self.height = dp(90)
        with self.canvas.before:
            self._farbe = Color(1, 1, 1, 0.04)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
            self._randfarbe = Color(*FARBE_RAND)
            self._rand = Line(width=1)
        self.bind(pos=self._update, size=self._update)

        self.phase_label = Label(text=phase_text, color=FARBE_AKZENT, bold=True, font_size="11sp")
        self.timer_label = Label(text="--:--", color=FARBE_TEXT, bold=True, font_size="26sp")
        self.sub_label = Label(text="ausgeschaltet", color=FARBE_DIM, font_size="12sp")
        self.add_widget(self.phase_label)
        self.add_widget(self.timer_label)
        self.add_widget(self.sub_label)

    def _update(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rand.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(14))

    def anzeigen(self, aktiv, phase_text, timer_text, sub_text):
        self.phase_label.text = phase_text
        self.timer_label.text = timer_text
        self.sub_label.text = sub_text
        ziel_farbe = (0.216, 0.741, 0.973, 0.12) if aktiv else (1, 1, 1, 0.04)
        ziel_rand = (0.216, 0.741, 0.973, 0.35) if aktiv else FARBE_RAND
        Animation.cancel_all(self._farbe)
        Animation.cancel_all(self._randfarbe)
        Animation(rgba=ziel_farbe, duration=0.2).start(self._farbe)
        Animation(rgba=ziel_rand, duration=0.2).start(self._randfarbe)

    def text_aktualisieren(self, phase_text=None, timer_text=None):
        """Leichtgewichtiges Update ohne Hintergrund-Animation, fuer den
        sekuendlichen Live-Tick (siehe App._anwesenheit_tick)."""
        if phase_text is not None:
            self.phase_label.text = phase_text
        if timer_text is not None:
            self.timer_label.text = timer_text


class StatusPunkt(Widget):
    """Kleiner Verbindungs-Punkt mit Puls-Animation im Online-Zustand, wie
    '.live-punkt' im Web-UI."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(10), dp(10)))
        super().__init__(**kwargs)
        with self.canvas:
            self._glow_farbe = Color(*FARBE_GRUEN[:3], 0)
            self._glow = Ellipse(pos=self.pos, size=self.size)
            self._farbe = Color(*FARBE_DIM)
            self._punkt = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._update, size=self._update)
        self._puls_event = None

    def _update(self, *_args):
        self._punkt.pos = self.pos
        self._punkt.size = self.size
        gw, gh = self.width * 3.2, self.height * 3.2
        self._glow.pos = (self.center_x - gw / 2, self.center_y - gh / 2)
        self._glow.size = (gw, gh)

    def status_setzen(self, status):
        if self._puls_event:
            self._puls_event.cancel()
            self._puls_event = None
        Animation.cancel_all(self._glow_farbe)
        if status == "online":
            self._farbe.rgba = FARBE_GRUEN
            self._puls_impuls()
            self._puls_event = Clock.schedule_interval(self._puls_impuls, 1.6)
        elif status == "offline":
            self._farbe.rgba = FARBE_ROT
            self._glow_farbe.a = 0
        else:
            self._farbe.rgba = FARBE_DIM
            self._glow_farbe.a = 0

    def _puls_impuls(self, *_args):
        Animation.cancel_all(self._glow_farbe)
        self._glow_farbe.rgba = (*FARBE_GRUEN[:3], 0.55)
        Animation(a=0, duration=1.4, t="out_quad").start(self._glow_farbe)


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


def frage_bestaetigen(titel, text, ja_callback):
    """Einfacher Ja/Nein-Dialog, wie confirm() im Web-UI bzw. MessageBox in
    der Windows-App (z.B. vor dem Loeschen einer Datei oder dem Hochladen
    eines Updates)."""
    inhalt = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(12))
    inhalt.add_widget(Label(text=text, color=FARBE_TEXT, font_size="13.5sp"))
    knopf_reihe = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
    inhalt.add_widget(knopf_reihe)

    popup = Popup(title=titel, content=inhalt, size_hint=(0.85, None), height=dp(160),
                   background_color=FARBE_LEISTE, separator_color=FARBE_RAND,
                   title_color=FARBE_TEXT)

    ja_btn = AktionsButton(text="Ja", height=dp(44))
    nein_btn = AktionsButton(text="Abbrechen", height=dp(44))

    def _ja(*_args):
        popup.dismiss()
        ja_callback()

    ja_btn.bind(on_release=_ja)
    nein_btn.bind(on_release=lambda *_: popup.dismiss())
    knopf_reihe.add_widget(nein_btn)
    knopf_reihe.add_widget(ja_btn)
    popup.open()


# --- Steuerung-Bildschirm ----------------------------------------------------

class SteuerungScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sperre = False  # verhindert Server-Aufrufe beim Anzeigen von Poll-Daten

        wurzel = ScrollView()
        self.inhalt = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(16), size_hint_y=None)
        self.inhalt.bind(minimum_height=self.inhalt.setter("height"))

        self.geraet_name_label = Label(text="", color=FARBE_AKZENT, bold=True, font_size="14sp",
                                        size_hint_y=None, height=dp(0))
        self.inhalt.add_widget(self.geraet_name_label)
        self.status_label = dim_label("Verbinde...")
        self.inhalt.add_widget(self.status_label)
        self.inhalt.add_widget(self._buttons_karte())
        self.inhalt.add_widget(self._automatik_karte())
        self.inhalt.add_widget(self._bewegung_karte())
        self.inhalt.add_widget(self._verlauf_karte())
        self.inhalt.add_widget(self._update_karte())

        wurzel.add_widget(self.inhalt)
        self.add_widget(wurzel)

    def status_setzen(self, text):
        self.status_label.text = text

    def geraet_name_setzen(self, name):
        self.geraet_name_label.text = name or ""
        self.geraet_name_label.height = dp(20) if name else dp(0)

    def _buttons_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("Steuerung"))
        # WICHTIG: minimum_height binden statt einer festen Hoehe - sonst
        # ist das Gitter niedriger als seine zwei Reihen an Buttons und die
        # untere Reihe ueberlappt die naechste Karte darunter.
        gitter = GridLayout(cols=2, spacing=dp(10), size_hint_y=None)
        gitter.bind(minimum_height=gitter.setter("height"))

        auf_btn = AktionsButton(text="Auf", icon_widget=PfeilIcon("auf"), height=dp(96))
        ab_btn = AktionsButton(text="Ab", icon_widget=PfeilIcon("ab"), height=dp(96))
        stehen_btn = AktionsButton(text="Stehen", icon_widget=PersonIcon(sitzend=False), height=dp(96))
        sitzen_btn = AktionsButton(text="Sitzen", icon_widget=PersonIcon(sitzend=True), height=dp(96))

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

        setzen_gitter = GridLayout(cols=2, spacing=dp(10), size_hint_y=None, height=dp(44))
        stehen_setzen_btn = AktionsButton(text="Stehposition setzen", height=dp(44))
        sitzen_setzen_btn = AktionsButton(text="Sitzposition setzen", height=dp(44))
        stehen_setzen_btn.text_label.font_size = "12sp"
        sitzen_setzen_btn.text_label.font_size = "12sp"
        stehen_setzen_btn.bind(on_release=lambda *_: app().aktion_senden("stehen_setzen"))
        sitzen_setzen_btn.bind(on_release=lambda *_: app().aktion_senden("sitzen_setzen"))
        setzen_gitter.add_widget(stehen_setzen_btn)
        setzen_gitter.add_widget(sitzen_setzen_btn)
        karte.add_widget(setzen_gitter)

        return karte

    def _automatik_karte(self):
        karte = Karte()
        kopf = BoxLayout(size_hint_y=None, height=dp(28))
        kopf.add_widget(titel_label("Automatik"))
        self.automatik_switch = Schalter(active=False)
        self.automatik_switch.bind(active=self._automatik_geaendert)
        schalter_huelle = AnchorLayout(size_hint_x=None, width=dp(46))
        schalter_huelle.add_widget(self.automatik_switch)
        kopf.add_widget(schalter_huelle)
        karte.add_widget(kopf)

        felder = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(10))
        self.sitzen_input = eingabe_feld("60")
        self.stehen_input = eingabe_feld("15")
        felder.add_widget(feld_mit_label("Sitzen (Min)", self.sitzen_input))
        felder.add_widget(feld_mit_label("Stehen (Min)", self.stehen_input))
        karte.add_widget(felder)

        self.automatik_status_box = StatusBox(phase_text="Automatik")
        karte.add_widget(self.automatik_status_box)
        return karte

    def _automatik_geaendert(self, _instance, wert):
        if self._sperre:
            return
        App.get_running_app().automatik_umschalten(
            wert, self.sitzen_input.text or "60", self.stehen_input.text or "15", "sitzen")

    def _bewegung_karte(self):
        karte = Karte()
        kopf = BoxLayout(size_hint_y=None, height=dp(28))
        kopf.add_widget(titel_label("Bewegungserkennung"))
        self.bewegung_switch = Schalter(active=True)
        self.bewegung_switch.bind(active=self._bewegung_geaendert)
        schalter_huelle = AnchorLayout(size_hint_x=None, width=dp(46))
        schalter_huelle.add_widget(self.bewegung_switch)
        kopf.add_widget(schalter_huelle)
        karte.add_widget(kopf)

        obere_felder = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(10))
        self.abfrage_input = eingabe_feld("1")
        self.schwellwert_input = eingabe_feld("1")
        obere_felder.add_widget(feld_mit_label("Abfrage (Sek.)", self.abfrage_input))
        obere_felder.add_widget(feld_mit_label(u"Änderung (cm)", self.schwellwert_input))
        karte.add_widget(obere_felder)

        self.timeout_input = eingabe_feld("10")
        karte.add_widget(feld_mit_label("Timeout ohne Bewegung (Min.)", self.timeout_input))

        self.bewegung_status_box = StatusBox(phase_text="Sensor")
        karte.add_widget(self.bewegung_status_box)
        return karte

    def _bewegung_geaendert(self, _instance, wert):
        if self._sperre:
            return
        App.get_running_app().anwesenheit_umschalten(
            wert, self.abfrage_input.text or "1", self.timeout_input.text or "10",
            self.schwellwert_input.text or "1")

    def bewegung_live_anzeigen(self, anwesenheit):
        """Leichtgewichtiges 1s-Update von cm-Anzeige/Timer, siehe
        App._anwesenheit_tick - ruehrt Schalter/Eingabefelder nicht an."""
        if not anwesenheit.get("aktiv"):
            return
        self.bewegung_status_box.text_aktualisieren(
            phase_text=anwesenheit_phase_text(anwesenheit),
            timer_text=format_zeit(anwesenheit.get("rest_sek", 0)))

    def _verlauf_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("Verlauf"))
        self.verlauf_liste = BoxLayout(orientation="vertical", spacing=dp(6),
                                        size_hint_y=None)
        self.verlauf_liste.bind(minimum_height=self.verlauf_liste.setter("height"))
        karte.add_widget(self.verlauf_liste)
        return karte

    def _verlauf_zeile(self, eintrag):
        zeile = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(10))

        icon_kachel = BoxLayout(size_hint=(None, None), size=(dp(28), dp(28)))
        with icon_kachel.canvas.before:
            Color(1, 1, 1, 0.06)
            icon_rect = RoundedRectangle(pos=icon_kachel.pos, size=icon_kachel.size, radius=[dp(8)])

        def _icon_update(inst, *_args, rect=icon_rect):
            rect.pos = inst.pos
            rect.size = inst.size

        icon_kachel.bind(pos=_icon_update, size=_icon_update)
        icon_kachel.add_widget(
            Label(text=VERLAUF_ICON.get(eintrag["aktion"], u"•"), font_size="13sp", color=FARBE_TEXT))

        text = VERLAUF_LABEL.get(eintrag["aktion"], eintrag["aktion"])
        badge = VERLAUF_BADGE.get(eintrag.get("quelle"))
        if badge:
            text += "   [{}]".format(badge)
        text_label = Label(text=text, color=FARBE_TEXT, font_size="13sp", halign="left", valign="middle")
        text_label.bind(size=text_label.setter("text_size"))

        zeit_label = Label(text=format_vor(eintrag.get("vor_sek", 0)), color=FARBE_DIM,
                            font_size="11.5sp", size_hint_x=None, width=dp(56))

        zeile.add_widget(icon_kachel)
        zeile.add_widget(text_label)
        zeile.add_widget(zeit_label)
        return zeile

    def _update_karte(self):
        karte = Karte()
        kopf = BoxLayout(size_hint_y=None, height=dp(28))
        kopf.add_widget(titel_label("Update"))
        karte.add_widget(kopf)

        self.update_datei_label = dim_label("Keine Datei gewaehlt")
        karte.add_widget(self.update_datei_label)

        reihe = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        waehlen_btn = AktionsButton(text="Datei waehlen...", height=dp(48))
        waehlen_btn.bind(on_release=self._update_datei_waehlen)
        self.update_hochladen_btn = AktionsButton(text="Hochladen", height=dp(48))
        self.update_hochladen_btn.disabled = True
        self.update_hochladen_btn.bind(on_release=self._update_hochladen)
        reihe.add_widget(waehlen_btn)
        reihe.add_widget(self.update_hochladen_btn)
        karte.add_widget(reihe)

        neustart_btn = AktionsButton(text="Pico neu starten", height=dp(44))
        neustart_btn.bind(on_release=self._neustart_bestaetigen)
        karte.add_widget(neustart_btn)

        self.update_status = dim_label(
            "Die Datei wird unter ihrem eigenen Namen gespeichert. .py-Dateien "
            "werden geprueft und starten den Pico danach neu."
        )
        karte.add_widget(self.update_status)

        self._update_datei_pfad = None
        return karte

    def _update_datei_waehlen(self, *_args):
        from plyer import filechooser
        filechooser.open_file(on_selection=self._update_datei_gewaehlt, multiple=False)

    @mainthread
    def _update_datei_gewaehlt(self, auswahl):
        if not auswahl:
            return
        self._update_datei_pfad = auswahl[0]
        self.update_datei_label.text = os.path.basename(self._update_datei_pfad)
        self.update_hochladen_btn.disabled = False

    def _update_hochladen(self, *_args):
        if not self._update_datei_pfad:
            return
        ziel = os.path.basename(self._update_datei_pfad)
        ist_python = ziel.lower().endswith(".py")
        text = '"{}" auf dem Pico speichern{}'.format(
            ziel, " und danach neu starten?" if ist_python else "?")
        frage_bestaetigen("Update hochladen", text, lambda: self._update_tatsaechlich_hochladen(ziel))

    def _update_tatsaechlich_hochladen(self, ziel):
        self.update_hochladen_btn.disabled = True
        self.update_status.text = "Lade hoch..."
        self.update_status.color = FARBE_DIM
        pfad = self._update_datei_pfad

        try:
            with open(pfad, "rb") as f:
                inhalt = f.read()
        except OSError as exc:
            self._update_ergebnis(False, str(exc))
            return

        def callback(erfolg, ergebnis):
            self._update_ergebnis(erfolg, ergebnis)

        App.get_running_app().datei_speichern(ziel, inhalt, callback)

    def _update_ergebnis(self, erfolg, ergebnis):
        self.update_hochladen_btn.disabled = False
        if erfolg and ergebnis.get("ok"):
            self.update_status.text = ("Update erfolgreich - Pico startet neu." if ergebnis.get("neustart")
                                        else "Aktualisiert - kein Neustart noetig.")
            self.update_status.color = FARBE_GRUEN
        elif erfolg:
            self.update_status.text = "Fehler: " + (ergebnis.get("fehler") or "unbekannt")
            self.update_status.color = FARBE_ROT
        else:
            self.update_status.text = "Verbindungsfehler: " + str(ergebnis)
            self.update_status.color = FARBE_ROT

    def _neustart_bestaetigen(self, *_args):
        frage_bestaetigen("Neustart", "Pico jetzt neu starten?", self._neustart_ausfuehren)

    def _neustart_ausfuehren(self):
        self.update_status.text = "Neustart..."
        self.update_status.color = FARBE_DIM
        App.get_running_app().neustart_anfordern()

    def daten_anzeigen(self, info, verlauf, automatik, anwesenheit):
        ip = info.get("ip") or "?"
        self.geraet_name_setzen(info.get("name"))
        self.status_setzen("Verbunden: {} (v{})".format(ip, info.get("version", "?")))

        self._sperre = True
        self.automatik_switch.active = bool(automatik.get("aktiv"))
        if automatik.get("aktiv"):
            phase = "Sitzen" if automatik.get("phase") == "sitzen" else "Stehen"
            naechste = "Stehen" if automatik.get("phase") == "sitzen" else "Sitzen"
            self.automatik_status_box.anzeigen(
                True, "Aktiv - {}".format(phase), format_zeit(automatik.get("rest_sek", 0)),
                'bis Wechsel zu "{}"'.format(naechste))
            if automatik.get("sitzen_min"):
                self.sitzen_input.text = str(automatik["sitzen_min"])
            if automatik.get("stehen_min"):
                self.stehen_input.text = str(automatik["stehen_min"])
        else:
            self.automatik_status_box.anzeigen(False, "Automatik", "--:--", "ausgeschaltet")

        self.bewegung_switch.active = bool(anwesenheit.get("aktiv"))
        if anwesenheit.get("aktiv"):
            self.bewegung_status_box.anzeigen(
                True, anwesenheit_phase_text(anwesenheit),
                format_zeit(anwesenheit.get("rest_sek", 0)), "bis Automatik-Stopp ohne Bewegung")
            if anwesenheit.get("abfrage_sek"):
                self.abfrage_input.text = str(int(anwesenheit["abfrage_sek"]))
            if anwesenheit.get("schwellwert_cm"):
                self.schwellwert_input.text = str(int(anwesenheit["schwellwert_cm"]))
            if anwesenheit.get("keine_aenderung_min"):
                self.timeout_input.text = str(int(anwesenheit["keine_aenderung_min"]))
        else:
            self.bewegung_status_box.anzeigen(False, "Sensor", "--:--", "ausgeschaltet")
        self._sperre = False

        self.verlauf_liste.clear_widgets()
        if not verlauf:
            self.verlauf_liste.add_widget(dim_label("Noch keine Aktionen"))
        for eintrag in verlauf:
            self.verlauf_liste.add_widget(self._verlauf_zeile(eintrag))


# --- Einstellungen-Bildschirm ------------------------------------------------

class EinstellungenScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        wurzel = ScrollView()
        self.inhalt = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(16), size_hint_y=None)
        self.inhalt.bind(minimum_height=self.inhalt.setter("height"))

        self.inhalt.add_widget(self._geraetename_karte())
        self.inhalt.add_widget(self._verbindung_karte())
        self.inhalt.add_widget(self._wlan_karte())
        if wifi_android.IST_ANDROID:
            self.inhalt.add_widget(self._hotspot_karte())

        wurzel.add_widget(self.inhalt)
        self.add_widget(wurzel)

    def _geraetename_karte(self):
        karte = Karte()
        karte.add_widget(titel_label("Geraetename"))
        karte.add_widget(dim_label("Zur eindeutigen Erkennung bei mehreren Picos"))

        self.name_input = TextInput(
            text="", hint_text="z.B. Schreibtisch Buero", multiline=False,
            foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.08),
            cursor_color=FARBE_AKZENT, size_hint_y=None, height=dp(40),
        )
        karte.add_widget(self.name_input)

        speichern_btn = AktionsButton(text="Namen speichern", height=dp(44))
        speichern_btn.bind(on_release=self._name_speichern)
        karte.add_widget(speichern_btn)

        self.name_speichern_status = dim_label("")
        karte.add_widget(self.name_speichern_status)
        return karte

    def _name_speichern(self, *_args):
        app = App.get_running_app()
        if not app.client:
            self.name_speichern_status.text = "Nicht mit dem Pico verbunden"
            self.name_speichern_status.color = FARBE_ROT
            return
        name = self.name_input.text.strip()
        if not name:
            self.name_speichern_status.text = "Name darf nicht leer sein"
            self.name_speichern_status.color = FARBE_ROT
            return

        self.name_speichern_status.text = "Speichere..."
        self.name_speichern_status.color = FARBE_DIM

        def callback(erfolg, fehler):
            if erfolg:
                self.name_speichern_status.text = "Gespeichert."
                self.name_speichern_status.color = FARBE_GRUEN
            else:
                self.name_speichern_status.text = "Fehler: " + fehler
                self.name_speichern_status.color = FARBE_ROT

        app.name_speichern(name, callback)

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

        if not self.name_input.text and app.letzte_info.get("name"):
            self.name_input.text = app.letzte_info["name"]

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


# --- Dateien-Bildschirm -------------------------------------------------------

class DateienScreen(Screen):
    """Dateien auf dem Pico suchen, bearbeiten, anlegen und loeschen - siehe
    dateien.html im Web-UI. Speichern/Anlegen laeuft ueber denselben
    /update-Mechanismus wie die Update-Karte auf dem Steuerung-Bildschirm."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dateien = []
        self.bearbeitete_datei = None  # None = es wird eine neue Datei angelegt

        self.unter_sm = ScreenManager(transition=NoTransition())
        self.unter_sm.add_widget(self._liste_screen())
        self.unter_sm.add_widget(self._editor_screen())
        self.add_widget(self.unter_sm)

    def _liste_screen(self):
        screen = Screen(name="liste")
        wurzel = ScrollView()
        inhalt = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(16), size_hint_y=None)
        inhalt.bind(minimum_height=inhalt.setter("height"))

        karte = Karte()
        karte.add_widget(titel_label("Dateien"))

        werkzeuge = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.suche_input = TextInput(
            text="", hint_text="Datei suchen...", multiline=False,
            foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.08),
            cursor_color=FARBE_AKZENT,
        )
        self.suche_input.bind(text=lambda *_a: self._liste_rendern())
        neu_btn = AktionsButton(text="+ Neu", size_hint_x=None, width=dp(84), height=dp(44))
        neu_btn.bind(on_release=lambda *_a: self._editor_oeffnen(None, ""))
        werkzeuge.add_widget(self.suche_input)
        werkzeuge.add_widget(neu_btn)
        karte.add_widget(werkzeuge)

        self.dateien_box = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
        self.dateien_box.bind(minimum_height=self.dateien_box.setter("height"))
        karte.add_widget(self.dateien_box)

        self.liste_status = dim_label("Lade Dateien...")
        karte.add_widget(self.liste_status)

        inhalt.add_widget(karte)
        wurzel.add_widget(inhalt)
        screen.add_widget(wurzel)
        return screen

    def _editor_screen(self):
        screen = Screen(name="editor")
        wurzel = ScrollView()
        inhalt = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12), size_hint_y=None)
        inhalt.bind(minimum_height=inhalt.setter("height"))

        karte = Karte()
        karte.add_widget(titel_label("Datei bearbeiten"))

        self.dateiname_input = TextInput(
            text="", hint_text="Dateiname", multiline=False,
            foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.08),
            cursor_color=FARBE_AKZENT, size_hint_y=None, height=dp(40),
        )
        karte.add_widget(self.dateiname_input)

        self.editor_inhalt_input = TextInput(
            text="", multiline=True,
            foreground_color=FARBE_TEXT, background_color=(1, 1, 1, 0.06),
            cursor_color=FARBE_AKZENT, size_hint_y=None, height=dp(320),
        )
        karte.add_widget(self.editor_inhalt_input)

        aktionen = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        speichern_btn = AktionsButton(text="Speichern", height=dp(48))
        speichern_btn.bind(on_release=self._speichern)
        abbrechen_btn = AktionsButton(text="Abbrechen", height=dp(48))
        abbrechen_btn.bind(on_release=lambda *_a: self._editor_schliessen())
        aktionen.add_widget(speichern_btn)
        aktionen.add_widget(abbrechen_btn)
        karte.add_widget(aktionen)

        self.editor_status = dim_label("")
        karte.add_widget(self.editor_status)

        inhalt.add_widget(karte)
        wurzel.add_widget(inhalt)
        screen.add_widget(wurzel)
        return screen

    # --- Liste --------------------------------------------------------------

    def _datei_zeile(self, eintrag):
        geschuetzt = eintrag["name"] in DATEIEN_GESCHUETZT
        zeile = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None, height=dp(82),
                           padding=(0, 0, 0, dp(8)))
        with zeile.canvas.before:
            Color(*FARBE_RAND)
            trenn_linie = Line(width=1)

        def _linie_update(inst, *_a, linie=trenn_linie):
            linie.points = [inst.x, inst.y, inst.x + inst.width, inst.y]

        zeile.bind(pos=_linie_update, size=_linie_update)

        info_zeile = BoxLayout(size_hint_y=None, height=dp(22))
        name_label = Label(text=eintrag["name"], color=FARBE_TEXT, font_size="13.5sp",
                            halign="left", valign="middle", shorten=True)
        name_label.bind(size=name_label.setter("text_size"))
        groesse_label = Label(text=format_groesse(eintrag["groesse"]), color=FARBE_DIM,
                               font_size="11sp", size_hint_x=None, width=dp(70),
                               halign="right", valign="middle")
        groesse_label.bind(size=groesse_label.setter("text_size"))
        info_zeile.add_widget(name_label)
        info_zeile.add_widget(groesse_label)
        zeile.add_widget(info_zeile)

        aktionen = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        bearbeiten_btn = AktionsButton(text="Bearbeiten", height=dp(38))
        bearbeiten_btn.bind(on_release=lambda *_a, n=eintrag["name"]: self._datei_bearbeiten(n))
        aktionen.add_widget(bearbeiten_btn)
        if not geschuetzt:
            loeschen_btn = AktionsButton(text="Loeschen", height=dp(38))
            loeschen_btn.bind(on_release=lambda *_a, n=eintrag["name"]: self._datei_loeschen_bestaetigen(n))
            aktionen.add_widget(loeschen_btn)
        zeile.add_widget(aktionen)
        return zeile

    def _liste_rendern(self):
        filter_text = self.suche_input.text.strip().lower()
        gefiltert = [d for d in self.dateien if filter_text in d["name"].lower()]
        self.dateien_box.clear_widgets()
        if not gefiltert:
            self.dateien_box.add_widget(
                dim_label("Keine Treffer" if self.dateien else "Keine Dateien gefunden"))
            return
        for eintrag in gefiltert:
            self.dateien_box.add_widget(self._datei_zeile(eintrag))

    def aktualisieren(self):
        app = App.get_running_app()
        if not app.client:
            self.liste_status.text = "Nicht mit dem Pico verbunden"
            self.liste_status.color = FARBE_ROT
            return
        self.liste_status.text = "Lade..."
        self.liste_status.color = FARBE_DIM

        def callback(erfolg, ergebnis):
            if erfolg:
                self.dateien = ergebnis
                self._liste_rendern()
                self.liste_status.text = ""
            else:
                self.liste_status.text = "Fehler: " + ergebnis
                self.liste_status.color = FARBE_ROT

        app.dateien_liste(callback)

    # --- Editor ---------------------------------------------------------------

    def _editor_oeffnen(self, name, inhalt):
        self.bearbeitete_datei = name
        self.dateiname_input.text = name or ""
        self.dateiname_input.disabled = name is not None
        self.editor_inhalt_input.text = inhalt or ""
        self.editor_status.text = ""
        self.unter_sm.current = "editor"

    def _editor_schliessen(self):
        self.unter_sm.current = "liste"
        self.bearbeitete_datei = None
        self.dateiname_input.disabled = False

    def _datei_bearbeiten(self, name):
        self.liste_status.text = 'Lade "{}"...'.format(name)
        self.liste_status.color = FARBE_DIM

        def callback(erfolg, ergebnis):
            if erfolg:
                self.liste_status.text = ""
                self._editor_oeffnen(name, ergebnis)
            else:
                self.liste_status.text = "Fehler: " + ergebnis
                self.liste_status.color = FARBE_ROT

        App.get_running_app().datei_lesen(name, callback)

    def _datei_loeschen_bestaetigen(self, name):
        frage_bestaetigen("Datei loeschen", '"{}" wirklich loeschen?'.format(name),
                           lambda: self._datei_loeschen(name))

    def _datei_loeschen(self, name):
        self.liste_status.text = "Loesche..."
        self.liste_status.color = FARBE_DIM

        def callback(erfolg, ergebnis):
            if erfolg and ergebnis.get("ok", True):
                self.liste_status.text = '"{}" geloescht'.format(name)
                self.liste_status.color = FARBE_GRUEN
                self.aktualisieren()
            else:
                fehler = ergebnis if isinstance(ergebnis, str) else ergebnis.get("fehler", "unbekannt")
                self.liste_status.text = "Fehler: " + fehler
                self.liste_status.color = FARBE_ROT

        App.get_running_app().datei_loeschen(name, callback)

    def _speichern(self, *_args):
        name = self.bearbeitete_datei or self.dateiname_input.text.strip()
        if not name:
            self.editor_status.text = "Bitte einen Dateinamen eingeben"
            self.editor_status.color = FARBE_ROT
            return
        inhalt = self.editor_inhalt_input.text
        if not inhalt.strip():
            self.editor_status.text = "Datei darf nicht leer sein"
            self.editor_status.color = FARBE_ROT
            return

        ist_python = name.lower().endswith(".py")
        text = '"{}" speichern{}'.format(name, " und Pico danach neu starten?" if ist_python else "?")
        frage_bestaetigen("Datei speichern", text, lambda: self._tatsaechlich_speichern(name, inhalt))

    def _tatsaechlich_speichern(self, name, inhalt):
        self.editor_status.text = "Speichere..."
        self.editor_status.color = FARBE_DIM

        def callback(erfolg, ergebnis):
            if erfolg and ergebnis.get("ok"):
                self.editor_status.text = "Gespeichert - Pico startet neu." if ergebnis.get("neustart") else "Gespeichert."
                self.editor_status.color = FARBE_GRUEN
                self._editor_schliessen()
                self.aktualisieren()
            elif erfolg:
                self.editor_status.text = "Fehler: " + (ergebnis.get("fehler") or "unbekannt")
                self.editor_status.color = FARBE_ROT
            else:
                self.editor_status.text = "Fehler: " + ergebnis
                self.editor_status.color = FARBE_ROT

        App.get_running_app().datei_speichern(name, inhalt.encode("utf-8"), callback)


# --- App ----------------------------------------------------------------------

class PicoSteuerungApp(App):
    def build(self):
        self.title = "Pico Steuerung"
        self.client = None
        self.verbunden = False
        self._anwesenheit_aktiv = False
        self.letzte_info = {}
        self.speicher = JsonStore(os.path.join(self.user_data_dir, "pico_app.json"))

        Window.clearcolor = FARBE_BG
        wifi_android.berechtigungen_anfordern()

        self.sm = ScreenManager(transition=NoTransition())
        self.steuerung_screen = SteuerungScreen(name="steuerung")
        self.einstellungen_screen = EinstellungenScreen(name="einstellungen")
        self.dateien_screen = DateienScreen(name="dateien")
        self.sm.add_widget(self.steuerung_screen)
        self.sm.add_widget(self.einstellungen_screen)
        self.sm.add_widget(self.dateien_screen)

        wurzel = BoxLayout(orientation="vertical")
        wurzel.add_widget(self._kopfleiste())
        wurzel.add_widget(self.sm)

        self._verbindung_wiederherstellen()
        Clock.schedule_interval(self._aktualisieren_tick, AKTUALISIERUNG_SEK)
        Clock.schedule_interval(self._anwesenheit_tick, ANWESENHEIT_TICK_SEK)

        return wurzel

    def _kopfleiste(self):
        leiste = BoxLayout(size_hint_y=None, height=dp(52), padding=(dp(16), 0), spacing=dp(10))
        with leiste.canvas.before:
            Color(*FARBE_LEISTE)
            self._leiste_rect = RoundedRectangle(pos=leiste.pos, size=leiste.size, radius=[0])
            Color(*FARBE_RAND)
            self._leiste_linie = Line(width=1)
        leiste.bind(pos=self._update_leiste, size=self._update_leiste)

        titel = Label(text="Pico Steuerung", color=FARBE_TEXT, bold=True, font_size="17sp",
                      halign="left", valign="middle")
        titel.bind(size=titel.setter("text_size"))
        leiste.add_widget(titel)

        self.verbindungspunkt = StatusPunkt()
        punkt_huelle = AnchorLayout(size_hint_x=None, width=dp(24))
        punkt_huelle.add_widget(self.verbindungspunkt)
        leiste.add_widget(punkt_huelle)

        dateien_btn = IconKnopf()
        dateien_btn.add_widget(OrdnerIcon())
        dateien_btn.bind(on_release=lambda *_a: self._bildschirm_umschalten("dateien"))
        leiste.add_widget(dateien_btn)

        einstellungen_btn = IconKnopf()
        einstellungen_btn.add_widget(MenuIcon())
        einstellungen_btn.bind(on_release=lambda *_a: self._bildschirm_umschalten("einstellungen"))
        leiste.add_widget(einstellungen_btn)
        return leiste

    def _update_leiste(self, instance, *_args):
        self._leiste_rect.pos = instance.pos
        self._leiste_rect.size = instance.size
        self._leiste_linie.points = [instance.x, instance.y, instance.x + instance.width, instance.y]

    def _bildschirm_umschalten(self, ziel):
        self.sm.current = "steuerung" if self.sm.current == ziel else ziel
        if self.sm.current == "einstellungen":
            self.einstellungen_screen.aktualisieren()
        elif self.sm.current == "dateien":
            self.dateien_screen.aktualisieren()

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
        self.verbindungspunkt.status_setzen("online")
        self.speicher.put("pico", ip=ip)
        self.steuerung_screen.status_setzen("Verbunden: " + ip)
        if self.sm.current == "einstellungen":
            self.einstellungen_screen.aktualisieren()
        elif self.sm.current == "dateien":
            self.dateien_screen.aktualisieren()

    @mainthread
    def _verbindung_verloren(self):
        self.verbunden = False
        self.verbindungspunkt.status_setzen("offline")
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
        self.verbindungspunkt.status_setzen("online")
        self._anwesenheit_aktiv = bool(anwesenheit.get("aktiv"))
        self.letzte_info = info
        self.steuerung_screen.daten_anzeigen(info, verlauf, automatik, anwesenheit)

    # --- Schnelles Live-Update der Entfernungsmessung (jede Sekunde, siehe
    # ANWESENHEIT_TICK_SEK) - unabhaengig vom 10s-Hauptzyklus, damit die
    # cm-Anzeige/der Timeout-Countdown wie im Web-UI live mitlaufen. -------

    def _anwesenheit_tick(self, _dt):
        if not (self.client and self._anwesenheit_aktiv):
            return
        threading.Thread(target=self._anwesenheit_tick_arbeit, daemon=True).start()

    def _anwesenheit_tick_arbeit(self):
        try:
            anwesenheit = self.client.anwesenheit_status()
        except PicoFehler:
            return  # Fehler wird beim naechsten regulaeren 10s-Sync behandelt
        self._anwesenheit_live_anzeigen(anwesenheit)

    @mainthread
    def _anwesenheit_live_anzeigen(self, anwesenheit):
        self.steuerung_screen.bewegung_live_anzeigen(anwesenheit)

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

    def anwesenheit_umschalten(self, aktiv, abfrage_sek, timeout_min, schwellwert_cm):
        # Optimistisch sofort setzen, damit der 1s-Live-Tick nicht erst bis
        # zum naechsten 10s-Sync auf den Server-Wert warten muss.
        self._anwesenheit_aktiv = bool(aktiv)
        if not self.client:
            return

        def arbeit():
            try:
                if aktiv:
                    self.client.anwesenheit_start(abfrage_sek, timeout_min, schwellwert_cm)
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

    # --- Geraetename ------------------------------------------------------

    def name_speichern(self, name, callback):
        client = self.client
        if not client:
            Clock.schedule_once(lambda _dt: callback(False, "Nicht mit dem Pico verbunden"))
            return

        def arbeit():
            try:
                client.name_speichern(name)
                Clock.schedule_once(lambda _dt: callback(True, ""))
            except PicoFehler as exc:
                fehler = str(exc)
                Clock.schedule_once(lambda _dt: callback(False, fehler))

        threading.Thread(target=arbeit, daemon=True).start()

    def neustart_anfordern(self):
        client = self.client
        if not client:
            return

        def arbeit():
            try:
                client.neustart()
            except PicoFehler:
                pass  # Verbindung bricht durch den Neustart erwartungsgemaess ab

        threading.Thread(target=arbeit, daemon=True).start()

    # --- Dateiverwaltung ----------------------------------------------------
    # (fuer den Dateien-Bildschirm sowie die Update-Karte auf dem Steuerung-
    # Bildschirm, die ebenfalls datei_speichern() fuer den Upload nutzt)

    def dateien_liste(self, callback):
        client = self.client
        if not client:
            Clock.schedule_once(lambda _dt: callback(False, "Nicht mit dem Pico verbunden"))
            return

        def arbeit():
            try:
                daten = client.dateien_liste()
                Clock.schedule_once(lambda _dt: callback(True, daten))
            except PicoFehler as exc:
                fehler = str(exc)
                Clock.schedule_once(lambda _dt: callback(False, fehler))

        threading.Thread(target=arbeit, daemon=True).start()

    def datei_lesen(self, name, callback):
        client = self.client
        if not client:
            Clock.schedule_once(lambda _dt: callback(False, "Nicht mit dem Pico verbunden"))
            return

        def arbeit():
            try:
                inhalt = client.datei_lesen(name)
                Clock.schedule_once(lambda _dt: callback(True, inhalt))
            except PicoFehler as exc:
                fehler = str(exc)
                Clock.schedule_once(lambda _dt: callback(False, fehler))

        threading.Thread(target=arbeit, daemon=True).start()

    def datei_loeschen(self, name, callback):
        client = self.client
        if not client:
            Clock.schedule_once(lambda _dt: callback(False, "Nicht mit dem Pico verbunden"))
            return

        def arbeit():
            try:
                ergebnis = client.datei_loeschen(name)
                Clock.schedule_once(lambda _dt: callback(True, ergebnis))
            except PicoFehler as exc:
                fehler = str(exc)
                Clock.schedule_once(lambda _dt: callback(False, fehler))

        threading.Thread(target=arbeit, daemon=True).start()

    def datei_speichern(self, ziel, inhalt_bytes, callback):
        """Speichert eine Datei unter ihrem eigenen Namen (identischer
        /update-Mechanismus wie ein main.py/index.html-Update) - genutzt vom
        Dateien-Editor (Speichern) und der Update-Karte (Datei-Upload)."""
        client = self.client
        if not client:
            Clock.schedule_once(lambda _dt: callback(False, "Nicht mit dem Pico verbunden"))
            return

        def arbeit():
            try:
                ergebnis = client.update_hochladen(ziel, inhalt_bytes)
                Clock.schedule_once(lambda _dt: callback(True, ergebnis))
            except PicoFehler as exc:
                fehler = str(exc)
                Clock.schedule_once(lambda _dt: callback(False, fehler))

        threading.Thread(target=arbeit, daemon=True).start()


if __name__ == "__main__":
    PicoSteuerungApp().run()
