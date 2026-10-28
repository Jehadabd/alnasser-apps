"""Live position for Google Earth Pro without any COM-port driver.

Google Earth opens a small .kml file containing a NetworkLink that re-downloads
http://127.0.0.1:47080/live.kml every second. This module serves that live.kml.
"""
from __future__ import annotations

import math
import os
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

HTTP_PORT = 47080
TRACK_POINTS = 1000


def _circle(lat, lon, radius_m, n=36):
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        dlat = radius_m * math.cos(a) / 111_320
        dlon = radius_m * math.sin(a) / (111_320 * max(math.cos(math.radians(lat)), 1e-6))
        pts.append(f"{lon + dlon:.7f},{lat + dlat:.7f},0")
    return " ".join(pts)


class LiveKml:
    def __init__(self, port: int = HTTP_PORT):
        self.port = port
        self._lock = threading.Lock()
        self._fix = None
        self._live = False
        self._model = ""
        self._track = deque(maxlen=TRACK_POINTS)
        self._httpd = None
        self.requests = 0                       # how many times Google Earth refreshed

    # -- data in ---------------------------------------------------------
    def update(self, fix: dict | None, model: str | None):
        with self._lock:
            self._live = fix is not None
            self._model = model or self._model
            if fix:
                self._fix = fix
                p = (round(fix["lon"], 7), round(fix["lat"], 7))
                if not self._track or self._track[-1] != p:
                    self._track.append(p)

    # -- KML out ---------------------------------------------------------
    def render(self, follow: bool) -> str:
        with self._lock:
            fix, live, model, track = self._fix, self._live, self._model, list(self._track)
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>GPS Link</name>',
                 '<Style id="live"><IconStyle><color>ff50d22a</color><scale>1.4</scale>'
                 '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon>'
                 '</IconStyle><LabelStyle><color>ff50d22a</color></LabelStyle></Style>',
                 '<Style id="lost"><IconStyle><color>ff3c3ce6</color><scale>1.4</scale>'
                 '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/forbidden.png</href></Icon>'
                 '</IconStyle><LabelStyle><color>ff3c3ce6</color></LabelStyle></Style>',
                 '<Style id="acc"><LineStyle><color>9950d22a</color><width>1.5</width></LineStyle>'
                 '<PolyStyle><color>3350d22a</color></PolyStyle></Style>',
                 '<Style id="track"><LineStyle><color>ff00a5ff</color><width>3</width></LineStyle></Style>']
        if fix:
            lat, lon = fix["lat"], fix["lon"]
            acc = fix.get("acc")
            name = "موقعي" if live else "آخر موقع — انقطعت الإشارة"
            desc = f"{escape(model)}<br/>{lat:.6f}, {lon:.6f}" + (f"<br/>±{acc:.0f} m" if acc else "")
            if live and follow:
                parts.append(f"<LookAt><longitude>{lon:.7f}</longitude><latitude>{lat:.7f}</latitude>"
                             "<altitude>0</altitude><range>350</range><tilt>0</tilt>"
                             "<altitudeMode>relativeToGround</altitudeMode></LookAt>")
            parts.append(f'<Placemark><name>{name}</name><description><![CDATA[{desc}]]></description>'
                         f'<styleUrl>#{"live" if live else "lost"}</styleUrl>'
                         f"<Point><coordinates>{lon:.7f},{lat:.7f},0</coordinates></Point></Placemark>")
            if live and acc and acc > 1:
                parts.append('<Placemark><name>الدقة</name><styleUrl>#acc</styleUrl><Polygon><outerBoundaryIs>'
                             f"<LinearRing><coordinates>{_circle(lat, lon, acc)}</coordinates></LinearRing>"
                             "</outerBoundaryIs></Polygon></Placemark>")
        if len(track) > 1:
            coords = " ".join(f"{x:.7f},{y:.7f},0" for x, y in track)
            parts.append('<Placemark><name>المسار</name><styleUrl>#track</styleUrl><LineString>'
                         f"<tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>")
        parts.append("</Document></kml>")
        return "".join(parts)

    def launcher(self, follow: bool) -> str:
        url = f"http://127.0.0.1:{self.port}/live.kml?follow={1 if follow else 0}"
        name = "GPS Link — مباشر" + (" (تتبع)" if follow else "")
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                '<kml xmlns="http://www.opengis.net/kml/2.2"><NetworkLink>'
                f"<name>{name}</name><open>1</open><flyToView>{1 if follow else 0}</flyToView>"
                f"<Link><href>{url}</href><refreshMode>onInterval</refreshMode>"
                "<refreshInterval>1</refreshInterval></Link></NetworkLink></kml>")

    def write_launcher(self, folder: str, follow: bool) -> str:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "GPS-Link-follow.kml" if follow else "GPS-Link.kml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.launcher(follow))
        return path

    # -- HTTP ------------------------------------------------------------
    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.path.startswith("/live.kml"):
                    self.send_error(404)
                    return
                owner.requests += 1
                body = owner.render(follow="follow=1" in self.path).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.google-earth.kml+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def open_in_google_earth(path: str) -> bool:
    """Open a .kml in Google Earth Pro (Windows). Returns False if it could not be launched."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Google Earth Pro\client\googleearth.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Google Earth Pro\client\googleearth.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Google Earth Pro\client\googleearth.exe"),
    ]
    import subprocess
    for exe in candidates:
        if os.path.isfile(exe):
            subprocess.Popen([exe, path])
            return True
    if hasattr(os, "startfile"):
        try:
            os.startfile(path)                  # .kml is associated with Google Earth on install
            return True
        except OSError:
            return False
    return False
