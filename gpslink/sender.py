"""Phone side: holds the latest GPS fix and serves it over TCP (reached from the PC through `adb forward`).

The PC sends a line back every second; if nothing arrives for LINK_TIMEOUT_S the phone
reports the link as lost (this is what happens when the USB-C cable is pulled).
"""
from __future__ import annotations

import socket
import threading
import time

from . import protocol

LINK_TIMEOUT_S = 3.0
STALE_FIX_S = 5.0


def accept_fix(new: dict, last: dict | None) -> bool:
    """Drop a much worse fix (e.g. network/cell) that arrives right after a good GPS fix."""
    if last is None:
        return True
    fresh = (new["ts"] - last["ts"]) < STALE_FIX_S
    new_acc, last_acc = new.get("acc") or 999, last.get("acc") or 999
    return not (fresh and new_acc > max(3 * last_acc, 20))


class SenderServer:
    def __init__(self, device_name: str, on_state=None, host="127.0.0.1", port=protocol.PORT):
        self.device_name = device_name
        self.on_state = on_state or (lambda s: None)
        self.host, self.port = host, port
        self._fix = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._srv = None
        self.link_alive = False
        self.was_linked = False      # lets the UI say 'connection lost' instead of 'waiting'

    # -- GPS input -------------------------------------------------------
    def push_fix(self, lat, lon, alt=None, spd=None, brg=None, acc=None, sats=None):
        fix = {"lat": float(lat), "lon": float(lon), "alt": alt, "spd": spd, "brg": brg,
               "acc": acc, "sats": sats, "ts": time.time()}
        with self._lock:
            if accept_fix(fix, self._fix):
                self._fix = fix

    def current_fix(self):
        with self._lock:
            if self._fix and time.time() - self._fix["ts"] < STALE_FIX_S:
                return dict(self._fix)
        return None

    # -- server ----------------------------------------------------------
    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(1)
        self._srv.settimeout(0.5)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._srv:
            self._srv.close()

    def _set_link(self, alive: bool):
        if alive != self.link_alive:
            self.link_alive = alive
            self.was_linked = self.was_linked or alive
            self.on_state({"link": alive})

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._serve(conn)          # one PC at a time
            self._set_link(False)

    def _serve(self, conn):
        conn.settimeout(0.2)
        last_rx = time.time()
        next_tx = 0.0
        got_ack = False
        try:
            conn.sendall(protocol.encode({"t": "hello", "model": self.device_name}))
            while not self._stop.is_set():
                now = time.time()
                if now >= next_tx:
                    fix = self.current_fix()
                    msg = {"t": "fix", **fix} if fix else {"t": "hb", "fix": False}
                    conn.sendall(protocol.encode(msg))
                    next_tx = now + protocol.HEARTBEAT_S
                try:
                    data = conn.recv(1024)
                    if not data:
                        return                  # PC closed the socket
                    last_rx, got_ack = now, True
                    self._set_link(True)
                except socket.timeout:
                    pass
                if got_ack and now - last_rx > LINK_TIMEOUT_S:
                    return                      # cable pulled / PC gone
                if not got_ack and now - last_rx > LINK_TIMEOUT_S * 2:
                    return                      # something connected but never talked
        except OSError:
            return
        finally:
            conn.close()
