#!/usr/bin/env bash
# Runs inside the Android emulator job. The emulator plays the phone.
set -x
APK=$(ls apk/*.apk | head -1)
adb install -r -g "$APK" || exit 1
adb shell settings put secure location_mode 3
adb emu geo fix 44.3661 33.3152
adb logcat -c
adb shell am start -n org.jihad.gpslink/org.kivy.android.PythonActivity
sleep 30                                   # Python + Kivy start-up on the emulator
mkdir -p shots
adb exec-out screencap -p > shots/phone-1-start.png
python3 tests/ci_e2e.py connect; CONNECT=$?
adb exec-out screencap -p > shots/phone-2-connected.png
adb logcat -d -s python:* > shots/phone-logcat.txt
python3 tests/ci_e2e.py unplug; UNPLUG=$?
echo "connect=$CONNECT unplug=$UNPLUG"
[ $CONNECT -eq 0 ] && [ $UNPLUG -eq 0 ]
