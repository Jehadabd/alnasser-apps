"""Run: python -m unittest discover -s tests -v   (from the project folder)"""
import os
import sys
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpslink import nmea, protocol                       # noqa: E402
from gpslink.receiver import Receiver, SerialNmeaOutput, parse_devices  # noqa: E402
from gpslink.kml import LiveKml                          # noqa: E402
from gpslink import adbsetup                             # noqa: E402
from gpslink.sender import SenderServer, accept_fix      # noqa: E402

PORT = 47123


def parse(sentence):
    body, cs = sentence.strip()[1:].split("*")
    return body.split(","), cs == nmea.checksum(body)


def to_deg(v, h):
    d = 2 if h in "NS" else 3
    x = int(v[:d]) + float(v[d:]) / 60
    return -x if h in "SW" else x


class NmeaTests(unittest.TestCase):
    def test_known_checksum(self):
        self.assertEqual(nmea.checksum("GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"), "47")

    def test_roundtrip_baghdad_and_southwest(self):
        now = datetime(2026, 10, 28, 16, 30, 5, tzinfo=timezone.utc)
        for lat, lon in [(33.315241, 44.366066), (-33.86785, -151.20732), (0.0, 0.0), (45.9999999, 9.9999999)]:
            for s in nmea.burst({"lat": lat, "lon": lon, "spd": 2.0, "brg": 90, "acc": 4}, now).splitlines():
                f, ok = parse(s)
                self.assertTrue(ok, s)
                if f[0] in ("GPGGA", "GPRMC"):
                    i = 2 if f[0] == "GPGGA" else 3
                    self.assertAlmostEqual(to_deg(f[i], f[i + 1]), lat, places=5)
                    self.assertAlmostEqual(to_deg(f[i + 2], f[i + 3]), lon, places=5)
                    self.assertNotIn("60.000", f[i] + f[i + 2])

    def test_no_fix_is_void(self):
        f, ok = parse(nmea.burst(None))
        self.assertTrue(ok)
        self.assertEqual((f[0], f[2]), ("GPRMC", "V"))


class FilterTests(unittest.TestCase):
    def test_drops_bad_fix_after_good(self):
        good = {"acc": 4, "ts": 100}
        self.assertFalse(accept_fix({"acc": 900, "ts": 101}, good))
        self.assertTrue(accept_fix({"acc": 6, "ts": 101}, good))
        self.assertTrue(accept_fix({"acc": 900, "ts": 110}, good))


class FakeAdb:
    def __init__(self):
        self.devs = []

    def devices(self):
        return list(self.devs)

    def forward(self, serial, port):
        pass


class FakeSerial:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b

    def close(self):
        pass


class PipelineTests(unittest.TestCase):
    def wait_for(self, states, want, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            if states and states[-1] == want:
                return
            time.sleep(0.05)
        self.fail(f"expected {want}, got {states}")

    def test_plug_fix_unplug(self):
        phone_link = []
        sender = SenderServer("Test Phone", on_state=lambda s: phone_link.append(s["link"]), port=PORT)
        sender.start()
        adb, ser, states = FakeAdb(), FakeSerial(), []
        kml = LiveKml(port=PORT + 10)
        rx = Receiver(on_state=lambda s, i: states.append(s), adb=adb, local_port=PORT, poll_s=0.2,
                      outputs=[SerialNmeaOutput("COM5", factory=lambda p: ser), kml])
        rx.start()
        try:
            self.wait_for(states, "NO_PHONE")
            adb.devs = [("ABC", "unauthorized", "ABC")]
            self.wait_for(states, "UNAUTHORIZED")
            adb.devs = [("ABC", "device", "Test Phone")]
            self.wait_for(states, "NO_FIX")                      # phone seen, no satellites yet
            self.assertIn("GPRMC", ser.data.decode())
            sender.push_fix(33.315241, 44.366066, alt=40, spd=0, brg=0, acc=3.5)
            self.wait_for(states, "CONNECTED")
            self.wait_for(phone_link, True)                       # phone also shows "connected"
            time.sleep(1.2)
            gga = [s for s in ser.data.decode().splitlines() if s.startswith("$GPGGA")][-1]
            f, ok = parse(gga)
            self.assertTrue(ok)
            self.assertAlmostEqual(to_deg(f[2], f[3]), 33.315241, places=5)
            self.assertIn("44.3660660,33.3152410", kml.render(follow=True))
            self.assertIn("<LookAt>", kml.render(follow=True))
            # cable pulled: adb no longer lists the phone
            adb.devs = []
            self.wait_for(states, "SIGNAL_LOST")
            self.wait_for(phone_link, False)                      # phone notices within ~3 s
            time.sleep(1.2)
            self.assertIn("انقطعت الإشارة", kml.render(follow=True))  # Google Earth shows it too
            self.assertNotIn("<LookAt>", kml.render(follow=True))
            # plug back in
            adb.devs = [("ABC", "device", "Test Phone")]
            sender.push_fix(33.3153, 44.3661, acc=3)
            self.wait_for(states, "CONNECTED")
        finally:
            rx.stop()
            sender.stop()
            rx.join(3)

    def test_phone_plugged_but_app_not_sending(self):
        adb, states = FakeAdb(), []
        adb.devs = [("ABC", "device", "Test Phone")]
        rx = Receiver(on_state=lambda s, i: states.append(s), adb=adb, local_port=PORT + 1, poll_s=0.2)
        rx.start()
        try:
            self.wait_for(states, "WAITING_APP")
        finally:
            rx.stop()
            rx.join(3)

    def test_no_adb(self):
        states = []
        rx = Receiver(on_state=lambda s, i: states.append(s), adb=None, local_port=PORT + 2)
        rx.start()
        try:
            self.wait_for(states, "NO_ADB")
        finally:
            rx.stop()
            rx.join(3)

    def test_garbage_input_is_ignored(self):
        self.assertIsNone(protocol.decode(b"\xff\xfe not json"))
        self.assertIsNone(protocol.decode(b"[1,2]"))


class KmlTests(unittest.TestCase):
    def test_valid_xml_and_http(self):
        import urllib.request
        import xml.etree.ElementTree as ET
        k = LiveKml(port=PORT + 20)
        k.start()
        try:
            for i in range(5):
                k.update({"lat": 33.3 + i * 1e-4, "lon": 44.3, "acc": 5, "spd": 1}, "Phone <&>")
            body = urllib.request.urlopen(f"http://127.0.0.1:{PORT + 20}/live.kml?follow=1", timeout=3).read()
            root = ET.fromstring(body)                     # well-formed even with <&> in the model name
            ns = "{http://www.opengis.net/kml/2.2}"
            self.assertEqual(len(root.findall(f".//{ns}Placemark")), 3)   # position, accuracy, track
            ET.fromstring(k.launcher(follow=True).encode())
            self.assertIn("refreshInterval>1<", k.launcher(follow=False))
            self.assertEqual(k.requests, 1)
        finally:
            k.stop()

    def test_empty_before_first_fix(self):
        import xml.etree.ElementTree as ET
        ET.fromstring(LiveKml().render(follow=True).encode())


class AdbTests(unittest.TestCase):
    def test_parse_devices_with_daemon_noise(self):
        out = ("* daemon not running; starting now at tcp:5037\n* daemon started successfully\n"
               "List of devices attached\nR58M123  device usb:1-1 product:a51 model:SM_A515F device:a51 transport_id:1\n"
               "XYZ  unauthorized usb:1-2 transport_id:2\n\n")
        self.assertEqual(parse_devices(out), [("R58M123", "device", "SM A515F"), ("XYZ", "unauthorized", "XYZ")])

    def test_download_extracts_only_adb(self):
        import http.server
        import io
        import tempfile
        import threading
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(f"platform-tools/{adbsetup.EXE}", "fake-adb")
            z.writestr("platform-tools/fastboot", "x")
            z.writestr("platform-tools/AdbWinApi.dll", "dll")
        payload = buf.getvalue()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            path = adbsetup.download_adb(d, url=f"http://127.0.0.1:{srv.server_port}/pt.zip")
            self.assertTrue(path.endswith(adbsetup.EXE))
            self.assertEqual(sorted(os.listdir(os.path.dirname(path))), sorted([adbsetup.EXE, "AdbWinApi.dll"]))
        srv.shutdown()

    def test_receiver_downloads_adb_when_missing(self):
        states, calls = [], []

        def provider():
            calls.append(1)
            raise OSError("no internet")
        rx = Receiver(on_state=lambda s, i: states.append(s), adb_provider=provider, local_port=PORT + 3)
        rx.start()
        time.sleep(1.5)
        rx.stop()
        rx.join(3)
        self.assertEqual(states[:2], ["GETTING_ADB", "NO_ADB"])
        self.assertEqual(len(calls), 1)                   # no hammering: retries every 30 s


if __name__ == "__main__":
    unittest.main()
