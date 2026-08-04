"""
LilyGO T-Display-S3 - Firmware flashen
========================================
Laedt die im Ordner mitgelieferte MicroPython-Firmware (firmware.bin, mit
dem "s3lcd"-Treiber fuer das eingebaute Display - siehe Docstring in
main.py) auf das Board.

Ablauf:
    1. Board per USB anschliessen und in den Bootloader-Modus versetzen
       (BOOT-Taste gedrueckt halten, kurz RESET druecken, dann beide
       loslassen - auf manchen Boards reicht auch nur BOOT gedrueckt
       halten waehrend des Einsteckens).
    2. Dieses Skript ausfuehren (oder flash_firmware.bat per Doppelklick).
    3. Es findet den seriellen Port automatisch (bei mehreren Treffern wird
       zur Auswahl aufgefordert), loescht den Flash-Speicher komplett und
       schreibt anschliessend firmware.bin an Offset 0x0.

Benoetigtes Paket:
    pip install esptool
"""

import os
import subprocess
import sys

FIRMWARE_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmware.bin")
CHIP = "esp32s3"
BAUDRATEN = ("460800", "115200")


def esptool_verfuegbar():
    try:
        import esptool  # noqa: F401
        return True
    except ImportError:
        return False


def esptool_installieren():
    print("esptool nicht gefunden - installiere es...")
    ergebnis = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "esptool"])
    return ergebnis.returncode == 0


def port_auswaehlen():
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    if not ports:
        print("[FEHLER] Kein serieller Port gefunden.")
        print("         Board per USB anschliessen und in den Bootloader-Modus")
        print("         versetzen (BOOT-Taste gedrueckt halten, kurz RESET")
        print("         druecken, danach beide loslassen).")
        return None

    if len(ports) == 1:
        port = ports[0].device
        print("Gefundener Port: {} ({})".format(port, ports[0].description))
        return port

    print("Mehrere serielle Ports gefunden:")
    for i, p in enumerate(ports, start=1):
        print("  [{}] {} - {}".format(i, p.device, p.description))
    while True:
        auswahl = input("Welchen Port verwenden? (Nummer eingeben): ").strip()
        if auswahl.isdigit() and 1 <= int(auswahl) <= len(ports):
            return ports[int(auswahl) - 1].device
        print("Ungueltige Eingabe, bitte erneut versuchen.")


def esptool_ausfuehren(*args):
    befehl = [sys.executable, "-m", "esptool"] + list(args)
    print("\n> " + " ".join(befehl))
    return subprocess.run(befehl).returncode == 0


def flash_loeschen(port):
    if esptool_ausfuehren("--chip", CHIP, "--port", port, "erase_flash"):
        return True
    print("[FEHLER] Loeschen fehlgeschlagen (siehe Meldungen oben).")
    print("         Board evtl. nicht im Bootloader-Modus - BOOT-Taste")
    print("         gedrueckt halten, kurz RESET druecken, dann erneut")
    print("         versuchen.")
    return False


def firmware_schreiben(port):
    for baud in BAUDRATEN:
        print("\nSchreibe Firmware (Baudrate {})...".format(baud))
        if esptool_ausfuehren(
            "--chip", CHIP, "--port", port, "--baud", baud,
            "write_flash", "-z", "0x0", FIRMWARE_DATEI,
        ):
            return True
        print('Fehlgeschlagen bei Baudrate {} - versuche es mit einer '
              'niedrigeren erneut.'.format(baud))
    return False


def main():
    print("============================================")
    print(" LilyGO T-Display-S3 - Firmware flashen")
    print("============================================\n")

    if not os.path.isfile(FIRMWARE_DATEI):
        print("[FEHLER] firmware.bin nicht gefunden neben diesem Skript:")
        print("         " + FIRMWARE_DATEI)
        return 1

    if not esptool_verfuegbar() and not esptool_installieren():
        print("[FEHLER] esptool konnte nicht installiert werden.")
        return 1

    port = port_auswaehlen()
    if not port:
        return 1

    print("\nACHTUNG: Der komplette Flash-Speicher des Boards wird geloescht")
    print("und mit firmware.bin ueberschrieben. Vorhandene Dateien (z.B.")
    print("main.py, tft_config.py) auf dem Board gehen dabei verloren -")
    print("nach dem Flashen muessen main.py/tft_config.py erneut per")
    print("Thonny o.ae. auf das Board kopiert werden.\n")
    bestaetigung = input('Fortfahren? Tippe "ja" zum Bestaetigen: ').strip().lower()
    if bestaetigung != "ja":
        print("Abgebrochen.")
        return 1

    print("\nLoesche Flash-Speicher...")
    if not flash_loeschen(port):
        return 1

    if not firmware_schreiben(port):
        print("\n[FEHLER] Schreiben der Firmware fehlgeschlagen (siehe Meldungen oben).")
        return 1

    print("\n============================================")
    print(" Firmware erfolgreich geflasht.")
    print(" Board einmal aus- und wieder einstecken (oder RESET druecken),")
    print(" danach main.py/tft_config.py per Thonny hochladen.")
    print("============================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
