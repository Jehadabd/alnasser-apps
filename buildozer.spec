[app]
title = GPS Link
package.name = gpslink
package.domain = org.jihad
source.dir = .
source.include_exts = py
source.exclude_dirs = tests, bin, .github, platform-tools, .venv, .buildozer, dist, build, apk, shots
version = 1.0

# python-bidi 0.4.2 is pure Python (newer versions need Rust and fail on Android)
requirements = python3,kivy,pyjnius,plyer,arabic-reshaper,python-bidi==0.4.2,six

orientation = portrait
fullscreen = 0

# INTERNET is required for the local TCP socket that adb forwards over the USB cable
android.permissions = ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION, INTERNET, WAKE_LOCK
android.wakelock = True
android.api = 34
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a, x86_64
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 0
