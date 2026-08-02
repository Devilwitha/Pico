"""
LilyGO T-Display-S3 - PC Monitor
=================================
Empfaengt CPU-, RAM- und GPU-Auslastung sowie die GPU-Temperatur per UDP
(Port 5005) und stellt die Werte auf dem eingebauten 320x170 ST7789-Display
mit Text und farbigen Fortschrittsbalken dar.

Voraussetzung: Das Display der T-Display-S3 haengt am 8-Bit-Parallelbus
(i80) und nicht an SPI. Dafuer wird eine MicroPython-Firmware benoetigt,
die den "s3lcd"-Treiber enthaelt (https://github.com/russhughes/s3lcd).
Zusaetzlich muessen die Font-Dateien "vga1_8x8.py" und "vga2_16x32.py"
aus diesem Projekt auf das Geraet kopiert werden.
"""

try:
    import ujson as json
except ImportError:
    import json

import socket
import time

import network
import s3lcd
from machine import Pin

import vga1_8x8 as small_font
import vga2_16x32 as big_font

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------
WIFI_SSID = "DEIN_WLAN_NAME"
WIFI_PASSWORD = "DEIN_WLAN_PASSWORT"

UDP_PORT = 5005

WIDTH = 320
HEIGHT = 170

BL_PIN = 15

# 8-Bit-Parallelbus-Pins der T-Display-S3
LCD_D0 = 39
LCD_D1 = 40
LCD_D2 = 41
LCD_D3 = 42
LCD_D4 = 45
LCD_D5 = 46
LCD_D6 = 47
LCD_D7 = 48
LCD_WR = 8
LCD_DC = 7
LCD_CS = 6
LCD_RST = 5

# --------------------------------------------------------------------------
# Hintergrundbeleuchtung einschalten
# --------------------------------------------------------------------------
backlight = Pin(BL_PIN, Pin.OUT)
backlight.value(1)

# --------------------------------------------------------------------------
# Display ueber den 8-Bit-Parallelbus initialisieren
# --------------------------------------------------------------------------
bus = s3lcd.Bus(
    dc=LCD_DC,
    wr=LCD_WR,
    freq=20000000,
    cs=LCD_CS,
    d0=LCD_D0,
    d1=LCD_D1,
    d2=LCD_D2,
    d3=LCD_D3,
    d4=LCD_D4,
    d5=LCD_D5,
    d6=LCD_D6,
    d7=LCD_D7,
)

tft = s3lcd.ST7789(
    bus,
    HEIGHT,
    WIDTH,
    reset=LCD_RST,
    rotation=1,
    color_order=s3lcd.BGR,
)
tft.init()
tft.fill(0)

# --------------------------------------------------------------------------
# Farben (RGB565)
# --------------------------------------------------------------------------
BLACK = s3lcd.color565(0, 0, 0)
WHITE = s3lcd.color565(255, 255, 255)
GRAY = s3lcd.color565(60, 60, 60)
GREEN = s3lcd.color565(0, 200, 0)
YELLOW = s3lcd.color565(230, 200, 0)
ORANGE = s3lcd.color565(240, 120, 0)
RED = s3lcd.color565(220, 0, 0)
CYAN = s3lcd.color565(0, 200, 220)

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

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("0.0.0.0", UDP_PORT))
    udp.settimeout(1.0)

    while True:
        try:
            data, addr = udp.recvfrom(512)
        except OSError:
            continue

        try:
            payload = json.loads(data)
        except ValueError:
            continue

        for index, row in enumerate(ROWS):
            key = row["key"]
            if key in payload:
                new_value = payload[key]
                if new_value != values[key]:
                    values[key] = new_value
                    draw_metric(index, new_value)


if __name__ == "__main__":
    main()
