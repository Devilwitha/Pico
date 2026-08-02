"""
PC Monitor Sender
==================
Liest CPU-, RAM- und GPU-Auslastung sowie die GPU-Temperatur aus und sendet
sie als JSON per UDP an das LilyGO T-Display-S3.

Benoetigte Pakete:
    pip install psutil
    pip install gputil      (optional, fuer GPU-Auslastung/-Temperatur)
"""

import json
import socket
import time

import psutil

try:
    import GPUtil
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------
TARGET_IP = "192.168.1.100"      # IP-Adresse des LilyGO T-Display-S3
TARGET_PORT = 5005
SEND_INTERVAL = 1.0              # Sekunden zwischen zwei Sendungen


def get_gpu_stats():
    if not GPU_AVAILABLE:
        return 0.0, 0.0

    gpus = GPUtil.getGPUs()
    if not gpus:
        return 0.0, 0.0

    gpu = gpus[0]
    gpu_load = round(gpu.load * 100, 1)
    gpu_temp = round(gpu.temperature, 1)
    return gpu_load, gpu_temp


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target_address = (TARGET_IP, TARGET_PORT)

    print("PC Monitor Sender gestartet")
    print("Ziel: {}:{}".format(TARGET_IP, TARGET_PORT))
    if not GPU_AVAILABLE:
        print("Hinweis: GPUtil nicht installiert - GPU-Werte werden als 0 gesendet.")

    # Der erste Aufruf initialisiert die interne Messung von psutil.
    psutil.cpu_percent(interval=None)
    time.sleep(0.1)

    try:
        while True:
            cpu_percent = psutil.cpu_percent(interval=None)
            ram_percent = psutil.virtual_memory().percent
            gpu_percent, gpu_temp = get_gpu_stats()

            payload = {
                "cpu": round(cpu_percent, 1),
                "ram": round(ram_percent, 1),
                "gpu": gpu_percent,
                "gpu_temp": gpu_temp,
            }

            message = json.dumps(payload).encode("utf-8")
            sock.sendto(message, target_address)

            print(payload)
            time.sleep(SEND_INTERVAL)
    except KeyboardInterrupt:
        print("Beendet durch Benutzer.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
