"""Build NMEA 0183 sentences (GGA, RMC, VTG) that Google Earth Pro understands."""
from datetime import datetime, timezone

KNOTS_PER_MPS = 1.943844
KMH_PER_MPS = 3.6


def checksum(body: str) -> str:
    """XOR of every character between '$' and '*'."""
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def _sentence(body: str) -> str:
    return f"${body}*{checksum(body)}\r\n"


def _coord(value: float, is_lat: bool):
    """Decimal degrees -> (ddmm.mmmmm, hemisphere). Rounds safely (never 60.00000 minutes)."""
    hemi = ("N" if value >= 0 else "S") if is_lat else ("E" if value >= 0 else "W")
    total = round(abs(value) * 60 * 100000)          # whole 1e-5 minutes
    deg, rem = divmod(total, 60 * 100000)
    minutes = rem / 100000
    width = 2 if is_lat else 3
    return f"{deg:0{width}d}{minutes:08.5f}", hemi


def _hdop(accuracy_m):
    # Phones report horizontal accuracy (metres), not HDOP. ~5 m per HDOP unit is a rough rule of thumb.
    if accuracy_m is None or accuracy_m <= 0:
        return 1.0
    return max(0.5, min(25.0, accuracy_m / 5.0))


def gga(fix: dict, now: datetime) -> str:
    lat, ns = _coord(fix["lat"], True)
    lon, ew = _coord(fix["lon"], False)
    sats = int(fix.get("sats") or 8)
    alt = fix.get("alt") or 0.0
    body = (f"GPGGA,{now:%H%M%S}.{now.microsecond // 10000:02d},{lat},{ns},{lon},{ew},"
            f"1,{sats:02d},{_hdop(fix.get('acc')):.1f},{alt:.1f},M,0.0,M,,")
    return _sentence(body)


def rmc(fix: dict, now: datetime) -> str:
    lat, ns = _coord(fix["lat"], True)
    lon, ew = _coord(fix["lon"], False)
    knots = (fix.get("spd") or 0.0) * KNOTS_PER_MPS
    course = (fix.get("brg") or 0.0) % 360
    body = (f"GPRMC,{now:%H%M%S}.{now.microsecond // 10000:02d},A,{lat},{ns},{lon},{ew},"
            f"{knots:.2f},{course:.1f},{now:%d%m%y},,,A")
    return _sentence(body)


def vtg(fix: dict) -> str:
    spd = fix.get("spd") or 0.0
    course = (fix.get("brg") or 0.0) % 360
    body = f"GPVTG,{course:.1f},T,,M,{spd * KNOTS_PER_MPS:.2f},N,{spd * KMH_PER_MPS:.2f},K,A"
    return _sentence(body)


def no_fix(now: datetime) -> str:
    """RMC with status V = receiver alive, no valid position yet."""
    return _sentence(f"GPRMC,{now:%H%M%S}.00,V,,,,,,,{now:%d%m%y},,,N")


def burst(fix, now=None) -> str:
    """All sentences for one 1-second epoch."""
    now = now or datetime.now(timezone.utc)
    if not fix:
        return no_fix(now)
    return gga(fix, now) + rmc(fix, now) + vtg(fix)
