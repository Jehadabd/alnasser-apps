"""Receiver without the GUI (and a demo mode to test Google Earth before the phone app is ready).

  python receiver_cli.py                 # real phone over USB -> live KML for Google Earth
  python receiver_cli.py --demo          # fake phone walking around Baghdad
  python receiver_cli.py --com COM5      # also write NMEA to a COM port (needs a virtual COM pair)
"""
import argparse
import os
import math
import threading
import time

from gpslink.adbsetup import ensure_adb
from gpslink.kml import LiveKml, open_in_google_earth
from gpslink.receiver import Receiver, SerialNmeaOutput

MESSAGES = {
    "GETTING_ADB": "First run: downloading adb...",
    "NO_ADB": "adb unavailable",
    "NO_PHONE": "Waiting for phone on USB...",
    "UNAUTHORIZED": "Phone plugged in - accept 'Allow USB debugging' on the phone",
    "WAITING_APP": "Phone detected - open the app and press Sender",
    "NO_FIX": "CONNECTED to phone - waiting for GPS fix",
    "CONNECTED": "CONNECTED - position flowing",
    "SIGNAL_LOST": "CONNECTION LOST - cable unplugged or app closed",
}


class DemoAdb:
    def devices(self):
        return [("DEMO", "device", "Demo Phone")]

    def forward(self, serial, port):
        pass


def start_demo_phone(port, lat, lon):
    from gpslink.sender import SenderServer
    phone = SenderServer("Demo Phone", port=port)
    phone.start()

    def walk():
        t = 0
        while True:
            a = t / 60 * 2 * math.pi                       # one 200 m circle per minute
            phone.push_fix(lat + 0.0009 * math.sin(a), lon + 0.0011 * math.cos(a),
                           alt=40, spd=3.5, brg=(math.degrees(a) + 90) % 360, acc=4)
            t += 1
            time.sleep(1)
    threading.Thread(target=walk, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--com", default=None, help="optional COM port for NMEA (Google Earth Realtime > Start)")
    ap.add_argument("--no-open", action="store_true", help="do not launch Google Earth")
    ap.add_argument("--demo", action="store_true", help="simulate a phone, no USB needed")
    ap.add_argument("--lat", type=float, default=33.3152)
    ap.add_argument("--lon", type=float, default=44.3661)
    a = ap.parse_args()

    if a.demo:
        port = 47099
        start_demo_phone(port, a.lat, a.lon)
        adb, provider = DemoAdb(), None
    else:
        port, adb, provider = 47001, None, ensure_adb

    def show(state, info):
        line = f"[{time.strftime('%H:%M:%S')}] {MESSAGES.get(state, state)}"
        if info.get("model"):
            line += f"  | phone: {info['model']}"
        if info.get("fix"):
            f = info["fix"]
            line += f"  | {f['lat']:.6f}, {f['lon']:.6f}  +/-{(f.get('acc') or 0):.0f} m"
        if info.get("error"):
            line += f"  | {info['error']}"
        print(line, flush=True)

    kml = LiveKml()
    kml.start()
    outputs = [kml] + ([SerialNmeaOutput(a.com)] if a.com else [])
    rx = Receiver(on_state=show, adb=adb, adb_provider=provider, outputs=outputs, local_port=port)
    rx.start()
    launcher = kml.write_launcher(os.path.dirname(os.path.abspath(__file__)), follow=True)
    print(f"Live KML: {launcher}  (open it in Google Earth Pro).  Ctrl+C to quit.")
    if not a.no_open:
        open_in_google_earth(launcher)
    try:
        while rx.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        rx.stop()


if __name__ == "__main__":
    main()
