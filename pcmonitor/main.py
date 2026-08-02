"""
LilyGO T-Display-S3 - PC Monitor
=================================
Empfaengt CPU-, RAM- und GPU-Auslastung sowie die GPU-Temperatur per UDP
(Port 5005) und stellt die Werte auf dem eingebauten 320x170 ST7789-Display
mit Text und farbigen Fortschrittsbalken dar.

Voraussetzung: Das Display der T-Display-S3 haengt am 8-Bit-Parallelbus
(i80) und nicht an SPI. Dafuer wird eine MicroPython-Firmware mit dem
"s3lcd"-Treiber benoetigt (https://github.com/russhughes/s3lcd) - Standard-
MicroPython-Firmware kennt dieses Modul nicht ("ImportError: no module
named 's3lcd'"). Passend fuer die T-Display-S3 (Octal-SPIRAM, 16MB Flash)
ist die vorkompilierte Firmware "firmware/GENERIC_S3_OCT_16M/firmware.bin"
aus diesem Repository. tft_config.py (liegt in diesem Ordner) muss
zusammen mit main.py auf das Geraet kopiert werden - die Bus-/Panel-Pins
(inkl. Power-Enable an Pin 15 und Hintergrundbeleuchtung an Pin 38) sind
dort bereits korrekt fuer dieses Board hinterlegt. Die verwendeten Fonts
(vga1_8x8, vga2_bold_16x32) sind in der Firmware bereits enthalten und
muessen nicht separat hochgeladen werden.
"""

try:
    import ujson as json
except ImportError:
    import json

import socket
import time

import network
import s3lcd
import tft_config

import vga1_8x8 as small_font
import vga2_bold_16x32 as big_font

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------
WIFI_SSID = "FRITZ!Box 5530 BA_2GEXT"
WIFI_PASSWORD = "1234567890"

UDP_PORT = 5005

# --------------------------------------------------------------------------
# Display initialisieren (Querformat 320x170, siehe tft_config.py)
# --------------------------------------------------------------------------
tft = tft_config.config(tft_config.WIDE)
tft.init()

WIDTH = tft.width()
HEIGHT = tft.height()

# --------------------------------------------------------------------------
# Farben (RGB565) - BLACK/WHITE/RED/GREEN/CYAN/YELLOW liefert s3lcd direkt,
# GRAY und ORANGE gibt es dort nicht und werden selbst gemischt.
# --------------------------------------------------------------------------
BLACK = s3lcd.BLACK
WHITE = s3lcd.WHITE
RED = s3lcd.RED
GREEN = s3lcd.GREEN
CYAN = s3lcd.CYAN
YELLOW = s3lcd.YELLOW
GRAY = s3lcd.color565(60, 60, 60)
ORANGE = s3lcd.color565(240, 120, 0)

# --------------------------------------------------------------------------
# Layout der Dashboard-Zeilen
# --------------------------------------------------------------------------
MARGIN = 10
BAR_WIDTH = WIDTH - 2 * MARGIN
BAR_HEIGHT = 12
ROW_HEIGHT = 34
FIRST_ROW_Y = 26
CHAR_WIDTH = 8

ROWS = [
    {"key": "cpu", "label": "CPU", "unit": "%", "max": 100},
    {"key": "ram", "label": "RAM", "unit": "%", "max": 100},
    {"key": "gpu", "label": "GPU", "unit": "%", "max": 100},
    {"key": "gpu_temp", "label": "GPU TEMP", "unit": " C", "max": 100},
]


def bar_color(percent, is_temp):
    if is_temp:
        if percent < 55:
            return GREEN
        if percent < 75:
            return YELLOW
        if percent < 85:
            return ORANGE
        return RED

    if percent < 60:
        return GREEN
    if percent < 80:
        return YELLOW
    if percent < 90:
        return ORANGE
    return RED


def draw_boot_screen(status_line, ip_line):
    tft.fill(BLACK)
    tft.text(big_font, "PC MONITOR", 40, 30, CYAN, BLACK)
    tft.text(small_font, status_line, 10, 90, WHITE, BLACK)
    tft.text(small_font, ip_line, 10, 110, WHITE, BLACK)
    tft.show()


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    while not wlan.isconnected():
        draw_boot_screen("WLAN: verbinde mit", "{}...".format(WIFI_SSID))
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)

        attempts = 0
        while not wlan.isconnected() and attempts < 40:
            time.sleep(0.5)
            attempts += 1

        if not wlan.isconnected():
            draw_boot_screen("WLAN Verbindung fehlgeschlagen", "Neuer Versuch...")
            time.sleep(2)

    ip_address = wlan.ifconfig()[0]
    draw_boot_screen("WLAN verbunden: " + WIFI_SSID, "IP: " + ip_address)
    time.sleep(2)
    return ip_address


def draw_dashboard_static():
    tft.fill(BLACK)
    tft.text(small_font, "PC MONITOR - UDP 5005", MARGIN, 4, CYAN, BLACK)
    tft.show()


def draw_metric(row_index, value):
    row = ROWS[row_index]
    y = FIRST_ROW_Y + row_index * ROW_HEIGHT

    tft.fill_rect(0, y, WIDTH, ROW_HEIGHT - 4, BLACK)

    label = row["label"]
    value_text = "{:.0f}{}".format(value, row["unit"])

    tft.text(small_font, label, MARGIN, y, WHITE, BLACK)

    value_x = WIDTH - MARGIN - len(value_text) * CHAR_WIDTH
    tft.text(small_font, value_text, value_x, y, WHITE, BLACK)

    percent = value
    if percent < 0:
        percent = 0
    if percent > row["max"]:
        percent = row["max"]

    color = bar_color(percent, row["key"] == "gpu_temp")

    bar_y = y + 16
    tft.fill_rect(MARGIN, bar_y, BAR_WIDTH, BAR_HEIGHT, GRAY)

    filled_width = int(BAR_WIDTH * percent / row["max"])
    if filled_width > 0:
        tft.fill_rect(MARGIN, bar_y, filled_width, BAR_HEIGHT, color)


def main():
    connect_wifi()

    draw_dashboard_static()

    values = {"cpu": 0, "ram": 0, "gpu": 0, "gpu_temp": 0}
    for index in range(len(ROWS)):
        draw_metric(index, values[ROWS[index]["key"]])
    tft.show()

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("0.0.0.0", UDP_PORT))
    udp.settimeout(1.0)

    while True:
        try:
            data, _addr = udp.recvfrom(512)
        except OSError:
            continue

        try:
            payload = json.loads(data)
        except ValueError:
            continue

        geaendert = False
        for index, row in enumerate(ROWS):
            key = row["key"]
            if key in payload:
                new_value = payload[key]
                if new_value != values[key]:
                    values[key] = new_value
                    draw_metric(index, new_value)
                    geaendert = True

        if geaendert:
            tft.show()


if __name__ == "__main__":
    main()
