# PyInstaller spec for the Windows receiver (built on GitHub Actions: windows-latest).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from kivy_deps import angle, glew, sdl2
from kivy.tools.packaging.pyinstaller_hooks import get_deps_minimal, hookspath, runtime_hooks

deps = get_deps_minimal(video=None, audio=None, camera=None, spelling=None)
deps["hiddenimports"] = list(deps.get("hiddenimports", [])) + [
    "serial", "serial.tools.list_ports", "arabic_reshaper", "bidi", "bidi.algorithm",
    *collect_submodules("gpslink"),
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=collect_data_files("arabic_reshaper"),
    hookspath=hookspath(),
    runtime_hooks=runtime_hooks(),
    noarchive=False,
    **deps,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GPSLink", console=False, upx=False)
coll = COLLECT(
    exe, a.binaries, a.datas,
    *[Tree(p) for p in (sdl2.dep_bins + glew.dep_bins + angle.dep_bins)],
    strip=False, upx=False, name="GPSLink",
)
