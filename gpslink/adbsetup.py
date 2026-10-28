"""Locate adb, or download Google's official platform-tools into the app folder on first run."""
from __future__ import annotations

import io
import os
import shutil
import sys
import urllib.request
import zipfile

URLS = {
    "win32": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
}
EXE = "adb.exe" if sys.platform == "win32" else "adb"
KEEP = {EXE, "AdbWinApi.dll", "AdbWinUsbApi.dll", "libwinpthread-1.dll"}


def app_dir() -> str:
    """Folder next to the program (works for python main.py and for the PyInstaller exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_adb(extra_dirs=()) -> str | None:
    for d in (*extra_dirs, os.path.join(app_dir(), "platform-tools"), app_dir()):
        p = os.path.join(d, EXE)
        if os.path.isfile(p):
            return p
    return shutil.which(EXE)


def download_adb(dest_root: str | None = None, url: str | None = None, timeout=60) -> str:
    """Download platform-tools and extract only adb (+ its DLLs). Returns the adb path."""
    url = url or URLS.get(sys.platform, URLS["linux"])
    dest = os.path.join(dest_root or app_dir(), "platform-tools")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        os.makedirs(dest, exist_ok=True)
        for info in z.infolist():
            name = os.path.basename(info.filename)
            if name in KEEP and not info.is_dir():
                target = os.path.join(dest, name)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                if name == EXE and sys.platform != "win32":
                    os.chmod(target, 0o755)
    path = os.path.join(dest, EXE)
    if not os.path.isfile(path):
        raise RuntimeError("adb not found inside the downloaded archive")
    return path


def ensure_adb() -> str:
    return find_adb() or download_adb()
