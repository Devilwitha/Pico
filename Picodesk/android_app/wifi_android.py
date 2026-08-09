"""Android-spezifische WLAN-Hilfsfunktionen: automatisches Verbinden mit dem
Pico-eigenen Recovery-Hotspot (siehe AP_SSID/AP_PASSWORT in main.py auf dem
Pico).

Wichtig: Seit Android 10 erlaubt Google Apps keine unsichtbare, direkte
WLAN-Verbindung mehr (Datenschutz). Der naechstliegende automatische Weg ist
eine "WifiNetworkSuggestion": die App registriert den Pico-Hotspot als
Vorschlag, und Android verbindet sich von selbst, sobald er in Reichweite
ist - allerdings muss der Nutzer App-Vorschlaege einmalig unter
Einstellungen -> WLAN -> Netzwerkvorschlaege erlauben. Als zuverlaessiger
Fallback (funktioniert auf allen Versionen/Herstellern) oeffnet
wifi_einstellungen_oeffnen() direkt die System-WLAN-Einstellungen.

Unter Nicht-Android (z.B. beim Testen auf dem Desktop) sind alle Funktionen
No-Ops, damit sich die App auch dort starten laesst.
"""

from kivy.utils import platform

IST_ANDROID = platform == "android"

AP_SSID_STANDARD = "PicoSteuerung-Setup"
AP_PASSWORT_STANDARD = "picosetup123"


def berechtigungen_anfordern():
    """Fragt die fuer WLAN-Funktionen noetigen Laufzeit-Berechtigungen an
    (nur unter Android relevant - die "normalen" WLAN-Berechtigungen werden
    bereits ueber buildozer.spec/das Manifest gewaehrt)."""
    if not IST_ANDROID:
        return
    try:
        from android.permissions import Permission, request_permissions

        request_permissions([
            Permission.ACCESS_FINE_LOCATION,
            Permission.ACCESS_WIFI_STATE,
            Permission.CHANGE_WIFI_STATE,
            Permission.CHANGE_NETWORK_STATE,
            Permission.ACCESS_NETWORK_STATE,
        ])
    except Exception:
        pass


def hotspot_vorschlagen(ssid=AP_SSID_STANDARD, passwort=AP_PASSWORT_STANDARD):
    """Registriert den Pico-Hotspot als WifiNetworkSuggestion, damit Android
    sich automatisch verbindet, sobald er in Reichweite ist. Gibt
    (erfolg: bool, meldung: str) zurueck."""
    if not IST_ANDROID:
        return False, "Nur unter Android verfuegbar"

    try:
        from jnius import autoclass

        Context = autoclass("android.content.Context")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        WifiNetworkSuggestionBuilder = autoclass("android.net.wifi.WifiNetworkSuggestion$Builder")
        ArrayList = autoclass("java.util.ArrayList")

        aktivitaet = PythonActivity.mActivity
        wifi_manager = aktivitaet.getSystemService(Context.WIFI_SERVICE)

        builder = WifiNetworkSuggestionBuilder()
        builder.setSsid(ssid)
        builder.setWpa2Passphrase(passwort)
        vorschlag = builder.build()

        liste = ArrayList()
        liste.add(vorschlag)

        status = wifi_manager.addNetworkSuggestions(liste)
        if status == 0:  # STATUS_NETWORK_SUGGESTIONS_SUCCESS
            return True, "Hotspot vorgeschlagen - Android verbindet automatisch, sobald er in Reichweite ist"
        if status == 6:  # STATUS_NETWORK_SUGGESTIONS_ERROR_ADD_DUPLICATE
            return True, "Hotspot war bereits als Vorschlag registriert"
        return False, "Android hat den Vorschlag abgelehnt (Code {})".format(status)
    except Exception as exc:
        return False, "Automatische Verbindung nicht moeglich: {}".format(exc)


def wifi_einstellungen_oeffnen():
    """Oeffnet die System-WLAN-Einstellungen als zuverlaessigen manuellen
    Weg, falls die automatische Verbindung (je nach Android-Version/
    Hersteller-Einschraenkungen) nicht funktioniert."""
    if not IST_ANDROID:
        return
    try:
        from jnius import autoclass

        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")

        intent = Intent(Settings.ACTION_WIFI_SETTINGS)
        PythonActivity.mActivity.startActivity(intent)
    except Exception:
        pass
