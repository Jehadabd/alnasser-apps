"""End-to-end test on CI: Android emulator (= the phone, running the real APK) -> adb -> Receiver -> live KML.

Usage: python tests/ci_e2e.py <phase>   phase = connect | unplug
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpslink.kml import LiveKml                       # noqa: E402
from gpslink.receiver import Adb, Receiver            # noqa: E402

LAT, LON = 33.3152, 44.3661


def geo_fix(lat, lon):
    subprocess.run(["adb", "emu", "geo", "fix", f"{lon}", f"{lat}"], capture_output=True, timeout=10)


def main(phase):
    states, fixes = [], []

    def on_state(s, info):
        states.append(s)
        if info.get("fix"):
            fixes.append(info["fix"])
        print(time.strftime("%H:%M:%S"), s, info.get("model") or "", flush=True)

    kml = LiveKml(port=47080)
    kml.start()
    rx = Receiver(on_state=on_state, adb=Adb(shutil.which("adb")), outputs=[kml])
    rx.start()

    if phase == "connect":
        end = time.time() + 120
        step = 0
        while time.time() < end and not (fixes and abs(fixes[-1]["lat"] - LAT) < 1e-3):
            geo_fix(LAT + step * 1e-5, LON)
            step += 1
            time.sleep(1)
        body = urllib.request.urlopen("http://127.0.0.1:47080/live.kml?follow=1", timeout=3).read().decode()
        ok = "CONNECTED" in states and fixes and abs(fixes[-1]["lat"] - LAT) < 1e-3 and "33.315" in body
        print("E2E CONNECT", "PASS" if ok else "FAIL", states[-5:], fixes[-1] if fixes else None, flush=True)
        rx.stop()
        return 0 if ok else 1

    if phase == "unplug":
        end = time.time() + 90
        while time.time() < end and "CONNECTED" not in states:
            geo_fix(LAT, LON)
            time.sleep(1)
        print("killing emulator = pulling the cable", flush=True)
        subprocess.run(["adb", "emu", "kill"], capture_output=True, timeout=20)
        end = time.time() + 30
        while time.time() < end and states[-1] != "SIGNAL_LOST":
            time.sleep(0.5)
        time.sleep(1.5)
        body = kml.render(follow=True)
        ok = "CONNECTED" in states and states[-1] == "SIGNAL_LOST" and "انقطعت الإشارة" in body
        print("E2E UNPLUG", "PASS" if ok else "FAIL", states[-5:], flush=True)
        rx.stop()
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
