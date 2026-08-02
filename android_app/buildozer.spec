[app]

title = Pico Steuerung
package.name = picosteuerung
package.domain = org.picosteuerung

source.dir = .
source.include_exts = py

version = 1.0.0

requirements = python3,kivy==2.3.0,pyjnius

orientation = portrait
fullscreen = 0

# Kein eigenes icon.filename gesetzt - buildozer verwendet das Standard-
# Kivy-Icon. Eigenes Icon: icon.png (kein Alpha-Kanal noetig) neben diese
# Datei legen und "icon.filename = %(source.dir)s/icon.png" ergaenzen.

# WiFi-Vorschlag (WifiNetworkSuggestion), Discovery per UDP-Broadcast und
# HTTP-Zugriff auf den Pico im lokalen Netz/Hotspot benoetigen diese
# Berechtigungen. ACCESS_FINE_LOCATION wird von Android fuer WLAN-Scan/
# -Verbindung auf aelteren Versionen vorausgesetzt.
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_STATE,CHANGE_NETWORK_STATE,ACCESS_FINE_LOCATION

# WifiNetworkSuggestion (android.net.wifi.WifiNetworkSuggestion) erfordert
# mindestens API 29 (Android 10) fuer den vollen Funktionsumfang; die App
# selbst laeuft auch auf aelteren Geraeten, der Hotspot-Vorschlag wird dort
# aber uebersprungen (siehe wifi_android.py).
android.minapi = 24
android.api = 34
android.ndk_api = 24

android.archs = arm64-v8a,armeabi-v7a

# Ohne diese Option fragt sdkmanager beim ersten Build interaktiv nach
# Zustimmung zu den Android-SDK-Lizenzen - das schlaegt im nicht-interaktiven
# Docker-Build fehl (EOF) und die Pakete (z.B. platform-tools) werden
# uebersprungen.
android.accept_sdk_license = True

# python-for-android master zeigt aktuell auf den frisch gemergten Release
# v2026.05.09, dessen hostpython3-Rezept in diesem Docker-Image nicht baut
# (Host-Python-Build bricht mit "Require native threads" / fehlenden POSIX-
# Typen wie pthread_mutex_t ab - die configure-Erkennung schlaegt in diesem
# Container fehl). Bis das behoben ist, auf den vorherigen stabilen Release
# fixieren.
p4a.branch = v2024.01.21

[buildozer]

log_level = 2
warn_on_root = 0
