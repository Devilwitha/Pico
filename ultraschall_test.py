"""
Grove Ultrasonic Ranger - Test-Skript fuer den Pico

Gibt die gemessene Distanz fortlaufend per print() aus. Dient zum
Testen der Verkabelung, bevor der Sensor in main.py eingebaut wird.

Verkabelung (Grove-Kabel -> Pico):
  Schwarz (GND) -> GND
  Rot     (VCC) -> 3V3 OUT (Pin 36)   ACHTUNG: nicht an 5V/VBUS, die
                   GPIOs des Pico sind nicht 5V-tolerant!
  Gelb    (SIG) -> ein freier GPIO, hier GP15
  Weiss   (NC)  -> nicht anschliessen

Als main.py oder per "import ultraschall_test" auf den Pico kopieren
und ausfuehren (z.B. mit Thonny), um die Werte in der Konsole zu sehen.
"""

from machine import Pin, time_pulse_us
import time

SIG_PIN = 15  # GPIO fuer die SIG-Leitung (gelbes Kabel) anpassen

# Timeout fuer die Echo-Messung in Mikrosekunden. Entspricht bei ~343 m/s
# Schallgeschwindigkeit einer maximalen Distanz von ca. 5 m.
TIMEOUT_US = 30_000


def distanz_cm(sig_pin=SIG_PIN, timeout_us=TIMEOUT_US):
    """Misst einmal die Distanz und gibt sie in cm zurueck (None bei Timeout)."""
    trigger = Pin(sig_pin, Pin.OUT)
    trigger.value(0)
    time.sleep_us(2)
    trigger.value(1)
    time.sleep_us(10)  # Trigger-Impuls, Datenblatt verlangt >= 10us
    trigger.value(0)

    echo = Pin(sig_pin, Pin.IN)
    dauer_us = time_pulse_us(echo, 1, timeout_us)
    if dauer_us < 0:
        return None  # kein Echo empfangen (Timeout oder zu nah/zu weit weg)

    return dauer_us / 58.0  # Umrechnung Laufzeit -> Zentimeter


if __name__ == "__main__":
    while True:
        d = distanz_cm()
        if d is None:
            print("Kein Echo empfangen")
        else:
            print("Distanz: {:.1f} cm".format(d))
        time.sleep(0.2)
