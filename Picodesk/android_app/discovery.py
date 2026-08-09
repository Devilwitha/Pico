"""UDP-Broadcast-Discovery: findet den Pico automatisch im WLAN oder in
dessen eigenem Recovery-Hotspot, ohne dass die IP-Adresse manuell
eingegeben werden muss.

Gegenstueck zu main.py auf dem Pico (DISCOVERY_PORT/DISCOVERY_ANFRAGE dort
muessen zu den Werten hier passen): die App schickt ein UDP-Broadcast-Paket,
der Pico antwortet direkt an den Absender mit seinen Geraetedaten (ip,
hostname, modus, version).
"""

import json
import socket
import time

DISCOVERY_PORT = 4210
DISCOVERY_ANFRAGE = b"PICO_DISCOVER"
DISCOVERY_TIMEOUT = 2.5


def pico_suchen(timeout=DISCOVERY_TIMEOUT):
    """Schickt eine Broadcast-Anfrage und sammelt Antworten bis zum
    Timeout. Gibt eine Liste gefundener Geraete zurueck (typischerweise
    genau eines), neueste zuerst gemeldete zuletzt."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)

    gefunden = []
    gesehene_ips = set()
    deadline = time.time() + timeout

    try:
        sock.sendto(DISCOVERY_ANFRAGE, ("255.255.255.255", DISCOVERY_PORT))
        while time.time() < deadline:
            try:
                daten, absender = sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                continue

            try:
                info = json.loads(daten.decode("utf-8"))
            except ValueError:
                continue

            if info.get("typ") != "pico":
                continue

            ip = info.get("ip") or absender[0]
            if ip in gesehene_ips:
                continue

            gesehene_ips.add(ip)
            info["ip"] = ip
            gefunden.append(info)
    finally:
        sock.close()

    return gefunden
