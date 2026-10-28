"""PC side: find the phone over USB (adb), pull fixes, hand them to outputs (live KML, optional COM/NMEA).

States reported through on_state(state, info):
  GETTING_ADB   downloading adb (first run only)
  NO_ADB        adb missing and download failed (info['error'])
  NO_PHONE      no phone on USB (cable unplugged)
  UNAUTHORIZED  phone plugged but "Allow USB debugging?" not accepted
  WAITING_APP   phone detected, but the app is not in Sender mode
  NO_FIX        connected to the phone, still searching for satellites
  CONNECTED     connected and GPS data flowing (info['fix'])
  SIGNAL_LOST   was connected, data stopped (cable pulled / app closed)
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time

from . import nmea, protocol

DATA_TIMEOUT_S = 3.0
FIX_MAX_AGE_S = 5.0
ADB_RETRY_S = 30.0
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # don't flash a console on Windows


class Adb:
    def __init__(self, path):
        self.path = path

    def _run(self, *args, timeout=15):
        r = subprocess.run([self.path, *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
        return r.stdout

    def devices(self):
        """[(serial, state, model)] — state is 'device', 'unauthorized', 'offline', ..."""
        return parse_devices(self._run("devices", "-l"))

    def forward(self, serial, port):
        self._run("-s", serial, "forward", f"tcp:{port}", f"tcp:{port}")


def parse_devices(text):
    out, started = [], False
    for line in text.splitlines():
        if line.startswith("List of devices"):
            started = True
            continue
        parts = line.split()
        if not started or len(parts) < 2 or line.startswith("*"):
            continue
        model = next((p[6:] for p in parts[2:] if p.startswith("model:")), parts[0])
        out.append((parts[0], parts[1], model.replace("_", " ")))
    return out


class SerialNmeaOutput:
    """Optional: NMEA to a COM port, for Google Earth's Tools > GPS > Realtime (needs a virtual COM pair)."""

    def __init__(self, port, factory=None):
        self.port = port
        self.factory = factory or _open_serial
        self.error = None
        self.writes = 0
        self._ser = None

    def update(self, fix, model):
        if self._ser is None:
            try:
                self._ser = self.factory(self.port)
                self.error = None
            except Exception as e:                       # noqa: BLE001 - surface any driver error
                self.error = str(e)
                return
        try:
            self._ser.write(nmea.burst(fix).encode("ascii"))
            self.writes += 1
        except Exception:                                # noqa: BLE001 - nobody reading the other end
            pass

    def close(self):
        if self._ser:
            self._ser.close()


def _open_serial(port):
    import serial                                       # pyserial, desktop only
    return serial.Serial(port, 4800, timeout=0, write_timeout=0.5)


class Receiver(threading.Thread):
    def __init__(self, on_state=None, adb=None, adb_provider=None, outputs=(),
                 local_port=protocol.PORT, poll_s=1.0):
        super().__init__(daemon=True)
        self.on_state = on_state or (lambda s, i: None)
        self.adb = adb
        self.adb_provider = adb_provider
        self.outputs = list(outputs)
        self.local_port = local_port
        self.poll_s = poll_s
        self._stop_evt = threading.Event()
        self._cur_state = None
        self._sock = None
        self._buf = b""
        self._last_rx = 0.0
        self._fix = None
        self._model = None
        self._next_adb_try = 0.0

    def stop(self):
        self._stop_evt.set()

    @property
    def state(self):
        return self._cur_state

    # -- helpers ---------------------------------------------------------
    def _set(self, state, **info):
        if state != self._cur_state or info.get("fix") is not None:
            self._cur_state = state
            self.on_state(state, info)

    def _close_sock(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock, self._buf = None, b""

    def _open_sock(self, serial):
        self.adb.forward(serial, self.local_port)
        s = socket.create_connection(("127.0.0.1", self.local_port), timeout=2)
        s.settimeout(0.2)
        self._sock, self._buf, self._last_rx = s, b"", time.time()

    def _read_lines(self):
        try:
            data = self._sock.recv(4096)
        except socket.timeout:
            return []
        if not data:
            raise ConnectionError("closed")
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        self._last_rx = time.time()
        return [m for m in map(protocol.decode, lines) if m]

    def _feed_outputs(self):
        fix = self._fix if self._fix and time.time() - self._fix["_rx"] < FIX_MAX_AGE_S else None
        for out in self.outputs:
            try:
                out.update(fix, self._model)
            except Exception:                            # noqa: BLE001 - one bad output must not stop the rest
                pass

    def _get_adb(self):
        if self.adb or not self.adb_provider or time.time() < self._next_adb_try:
            return self.adb
        self._set("GETTING_ADB")
        try:
            path = self.adb_provider()
            self.adb = Adb(path)
        except Exception as e:                           # noqa: BLE001
            self._next_adb_try = time.time() + ADB_RETRY_S
            self._set("NO_ADB", error=str(e))
        return self.adb

    # -- main loop -------------------------------------------------------
    def run(self):
        next_out = next_poll = next_open = 0.0
        phone = None
        was_connected = False
        while not self._stop_evt.is_set():
            now = time.time()
            if now >= next_poll:
                next_poll = now + self.poll_s
                phone = self._poll_phone(was_connected)
                if phone is None:
                    was_connected = False
            if phone and self._sock is None and now >= next_open:
                next_open = now + 1.0
                try:
                    self._open_sock(phone[0])
                except (OSError, subprocess.SubprocessError):
                    self._set("SIGNAL_LOST" if was_connected else "WAITING_APP", model=phone[2])
            if self._sock:
                try:
                    for msg in self._read_lines():
                        was_connected = self._on_message(msg, phone) or was_connected
                    if time.time() - self._last_rx > DATA_TIMEOUT_S:
                        raise ConnectionError("timeout")
                    self._sock.sendall(b"ack\n")
                except (OSError, ConnectionError):
                    self._close_sock()
                    self._fix = None
                    self._set("SIGNAL_LOST" if was_connected else "WAITING_APP",
                              model=phone[2] if phone else self._model)
            if now >= next_out:
                next_out = now + 1.0
                self._feed_outputs()
            time.sleep(0.1)
        self._close_sock()
        for out in self.outputs:
            if hasattr(out, "close"):
                out.close()

    def _poll_phone(self, was_connected):
        adb = self._get_adb()
        if adb is None:
            if not self.adb_provider:
                self._set("NO_ADB", error="adb not found")
            return None
        try:
            devs = adb.devices()
        except (OSError, subprocess.SubprocessError) as e:
            self._set("NO_ADB", error=str(e))
            return None
        ready = [d for d in devs if d[1] == "device"]
        if ready:
            return ready[0]
        self._close_sock()
        self._fix = None
        if any(d[1] == "unauthorized" for d in devs):
            self._set("UNAUTHORIZED")
        elif was_connected or self._cur_state in ("CONNECTED", "NO_FIX", "SIGNAL_LOST"):
            self._set("SIGNAL_LOST", model=self._model, reason="usb")
        else:
            self._set("NO_PHONE")
        return None

    def _on_message(self, msg, phone):
        kind = msg.get("t")
        if kind == "hello":
            self._model = str(msg.get("model") or (phone[2] if phone else "?"))[:60]
            self._set("NO_FIX", model=self._model)
            return True
        if kind == "hb":
            self._set("NO_FIX", model=self._model)
            return True
        if kind == "fix" and _valid(msg):
            msg["_rx"] = time.time()
            self._fix = msg
            self._set("CONNECTED", model=self._model, fix=msg)
            return True
        return False


def _valid(m):
    try:
        return -90 <= float(m["lat"]) <= 90 and -180 <= float(m["lon"]) <= 180
    except (KeyError, TypeError, ValueError):
        return False
