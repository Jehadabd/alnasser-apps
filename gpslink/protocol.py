"""Wire format between phone (sender) and PC (receiver): one JSON object per line."""
import json

PORT = 47001          # TCP port on the phone; adb forwards the same port on the PC
HEARTBEAT_S = 1.0     # sender pushes at least one line per second


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes):
    try:
        msg = json.loads(line.decode("utf-8"))
        return msg if isinstance(msg, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None
