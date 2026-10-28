"""GPS Link — one app, two modes.

  Android  -> "مرسل"   : reads the phone GPS and sends it over the USB cable.
  Windows  -> "مستقبل" : detects the phone, receives the position and shows it live in Google Earth Pro.

Extra command-line flags (used by the automated build):
  --selftest   run the desktop pipeline without a window and exit 0/1
  --smoke N    open the window and close it after N seconds
"""
import os
import sys
import tempfile


def selftest():
    """Headless check of everything the desktop receiver needs (runs inside the built .exe on CI)."""
    import time
    import urllib.request
    from gpslink.kml import LiveKml
    from gpslink.receiver import Receiver
    from gpslink.sender import SenderServer

    class DemoAdb:
        def devices(self):
            return [("DEMO", "device", "Demo")]

        def forward(self, s, p):
            pass

    phone = SenderServer("Demo", port=47199)
    phone.start()
    phone.push_fix(33.3152, 44.3661, acc=4)
    kml = LiveKml(port=47198)
    kml.start()
    states = []
    rx = Receiver(on_state=lambda s, i: states.append(s), adb=DemoAdb(), outputs=[kml], local_port=47199)
    rx.start()
    time.sleep(3)
    body = urllib.request.urlopen("http://127.0.0.1:47198/live.kml?follow=1", timeout=3).read().decode()
    ok = "CONNECTED" in states and "44.3661000,33.3152000" in body
    print("SELFTEST", "PASS" if ok else "FAIL", states, flush=True)
    return 0 if ok else 1


if "--selftest" in sys.argv:
    sys.exit(selftest())

os.environ.setdefault("KIVY_NO_ARGS", "1")

from kivy.app import App                                          # noqa: E402
from kivy.clock import Clock                                      # noqa: E402
from kivy.core.window import Window                               # noqa: E402
from kivy.graphics import Color, Line, RoundedRectangle           # noqa: E402
from kivy.metrics import dp, sp                                   # noqa: E402
from kivy.uix.boxlayout import BoxLayout                          # noqa: E402
from kivy.uix.button import Button                                # noqa: E402
from kivy.uix.checkbox import CheckBox                            # noqa: E402
from kivy.uix.label import Label                                  # noqa: E402
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager  # noqa: E402
from kivy.uix.spinner import Spinner                              # noqa: E402
from kivy.utils import platform                                   # noqa: E402

from gpslink import protocol                                      # noqa: E402
from gpslink.sender import SenderServer                           # noqa: E402

IS_ANDROID = platform == "android"

# ---------------------------------------------------------------- Arabic text
try:
    import arabic_reshaper
    try:
        from bidi import get_display            # python-bidi >= 0.5
    except ImportError:
        from bidi.algorithm import get_display  # python-bidi 0.4.x (Android build)

    def ar(text):
        return "\n".join(get_display(arabic_reshaper.reshape(t)) for t in str(text).split("\n"))
except ImportError:
    def ar(text):
        return text

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
    "/system/fonts/NotoNaskhArabic-Regular.ttf", "/system/fonts/NotoNaskhArabicUI-Regular.ttf",
    "/system/fonts/NotoSansArabic-Regular.ttf", "/system/fonts/DroidNaskh-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONT = next((f for f in FONT_CANDIDATES if os.path.isfile(f)), None)

# ---------------------------------------------------------------- design tokens
BG = (0.067, 0.086, 0.114, 1)        # #11161D page
SURFACE = (0.102, 0.129, 0.169, 1)   # #1A212B cards
TEXT = (0.933, 0.949, 0.965, 1)      # #EEF2F6
MUTED = (0.604, 0.651, 0.706, 1)     # #9AA6B4
OK = (0.169, 0.714, 0.451, 1)        # #2BB673 connected
WARN = (0.949, 0.643, 0.165, 1)      # #F2A42A partial
ERR = (0.898, 0.282, 0.302, 1)       # #E5484D lost
ACCENT = (0.231, 0.510, 0.965, 1)    # #3B82F6 primary action
IDLE = (0.239, 0.282, 0.341, 1)      # #3D4857 not yet
ON_COLOR = (0.06, 0.07, 0.09, 1)     # dark text on bright fills
FS_H1, FS_H2, FS_BODY, FS_SMALL = 26, 18, 15, 13


def L(text="", size=FS_BODY, color=TEXT, **kw):
    lb = Label(text=ar(text), font_size=sp(size), color=color, halign="center", valign="middle", **kw)
    if FONT:
        lb.font_name = FONT
    lb.bind(size=lambda w, s: setattr(w, "text_size", s))
    return lb


def B(text, color, on_press, size=FS_H2, **kw):
    b = Button(text=ar(text), font_size=sp(size), background_normal="", background_down="",
               background_color=color, color=TEXT, **kw)
    if FONT:
        b.font_name = FONT
    b.bind(on_press=lambda *_: on_press())
    return b


def set_text(label, text):
    label.text = ar(text)


def rtl(box, *widgets):
    """Add widgets so the first one sits on the right (Arabic reading order)."""
    for w in reversed(widgets):
        box.add_widget(w)


class Panel(BoxLayout):
    """Rounded surface; tint() changes its fill to show state."""

    def __init__(self, fill=SURFACE, radius=18, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("padding", dp(14))
        kw.setdefault("spacing", dp(4))
        super().__init__(**kw)
        with self.canvas.before:
            self._c = Color(*fill)
            self._r = RoundedRectangle(radius=[dp(radius)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._r.pos, self._r.size = self.pos, self.size

    def tint(self, rgba):
        self._c.rgba = rgba


class ChainNode(Panel):
    """One link of the signal chain: name + a status word (never colour alone)."""

    def __init__(self, name, **kw):
        super().__init__(fill=IDLE, radius=14, padding=dp(6), spacing=0, **kw)
        self.name = L(name, FS_BODY, TEXT)
        self.word = L("…", FS_SMALL, TEXT)
        self.add_widget(self.name)
        self.add_widget(self.word)

    def show(self, level, word):
        self.tint({"ok": OK, "warn": WARN, "err": ERR}.get(level, IDLE))
        fg = ON_COLOR if level in ("ok", "warn") else TEXT
        self.name.color = self.word.color = fg
        set_text(self.word, word)


class Chain(BoxLayout):
    """Nodes joined by a line — the screen's one visual idea: where does the signal stop?"""

    def __init__(self, names, **kw):
        super().__init__(spacing=dp(10), **kw)
        self.nodes = [ChainNode(n) for n in names]
        with self.canvas.before:
            Color(*IDLE)
            self._line = Line(width=dp(1.5))
        self.bind(pos=self._sync, size=self._sync)
        rtl(self, *self.nodes)

    def _sync(self, *_):
        y = self.center_y
        self._line.points = [self.x + dp(8), y, self.right - dp(8), y]


def headline_card():
    card = Panel(size_hint_y=None, height=dp(150), padding=dp(18))
    big = L("", FS_H1)
    sub = L("", FS_BODY, MUTED)
    card.add_widget(big)
    card.add_widget(sub)
    return card, big, sub


def paint_headline(card, big, sub, level, title, hint):
    card.tint({"ok": OK, "warn": WARN, "err": ERR}.get(level, SURFACE))
    fg = ON_COLOR if level in ("ok", "warn") else TEXT
    big.color = fg
    sub.color = fg if level in ("ok", "warn", "err") else MUTED
    set_text(big, title)
    set_text(sub, hint)


# ================================================================= home
class Home(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        box = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        box.add_widget(L("GPS Link", 34, size_hint_y=0.2))
        box.add_widget(L("الهاتف يرسل موقعه عبر كابل USB إلى Google Earth على الكمبيوتر", FS_BODY, MUTED,
                         size_hint_y=0.14))
        send = B("مرسل — الهاتف", OK if IS_ANDROID else IDLE, lambda: self.go("sender"), 22)
        recv = B("مستقبل — الكمبيوتر", IDLE if IS_ANDROID else ACCENT, lambda: self.go("receiver"), 22)
        send.disabled, recv.disabled = not IS_ANDROID, IS_ANDROID
        box.add_widget(send)
        box.add_widget(recv)
        box.add_widget(L("هذا الجهاز " + ("هاتف: وضع «مرسل»" if IS_ANDROID else "كمبيوتر: وضع «مستقبل»"),
                         FS_SMALL, MUTED, size_hint_y=0.12))
        self.add_widget(box)

    def go(self, name):
        self.manager.current = name


# ================================================================= sender (Android)
class SenderScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.server = None
        self.gps_on = False
        self.gps_off_reason = None
        self._tick_ev = None
        root = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(14))
        root.add_widget(L("مرسل", FS_H2, MUTED, size_hint_y=None, height=dp(30)))
        self.card, self.big, self.sub = headline_card()
        root.add_widget(self.card)
        self.chain = Chain(["GPS", "الكابل", "الكمبيوتر"], size_hint_y=None, height=dp(78))
        root.add_widget(self.chain)
        self.coords = L("", FS_H2)
        self.acc = L("", FS_BODY, MUTED)
        root.add_widget(self.coords)
        root.add_widget(self.acc)
        root.add_widget(L("أبقِ التطبيق مفتوحاً — الشاشة لن تنطفئ", FS_SMALL, MUTED,
                          size_hint_y=None, height=dp(28)))
        root.add_widget(B("رجوع", IDLE, self.back, FS_BODY, size_hint_y=None, height=dp(52)))
        self.add_widget(root)

    def on_enter(self):
        if IS_ANDROID:
            from android.permissions import Permission, request_permissions
            request_permissions([Permission.ACCESS_FINE_LOCATION, Permission.ACCESS_COARSE_LOCATION],
                                self._perm_result)
        else:
            self._start_server()
        if self._tick_ev is None:
            self._tick_ev = Clock.schedule_interval(self.tick, 0.5)
        self.tick()

    def on_leave(self):
        if self._tick_ev:
            self._tick_ev.cancel()
            self._tick_ev = None

    def _perm_result(self, perms, grants):
        if not all(grants):
            self.gps_off_reason = "لم يُسمح بالوصول للموقع — اسمح به من الإعدادات"
            return
        Clock.schedule_once(lambda *_: self._start())

    def _start_server(self):
        if self.server is None:
            self.server = SenderServer(device_model())
            try:
                self.server.start()
            except OSError as e:
                self.server = None
                self.gps_off_reason = f"تعذّر فتح المنفذ {protocol.PORT}: {e}"

    def _start(self):
        self._start_server()
        if not self.gps_on:
            from plyer import gps
            gps.configure(on_location=self._on_location, on_status=self._on_status)
            gps.start(minTime=1000, minDistance=0)
            self.gps_on = True

    def _on_location(self, **kw):            # Java thread
        if self.server:
            self.server.push_fix(kw["lat"], kw["lon"], alt=kw.get("altitude"), spd=kw.get("speed"),
                                 brg=kw.get("bearing"), acc=kw.get("accuracy"))
            self.gps_off_reason = None

    def _on_status(self, stype, status):
        if "disabled" in str(status).lower():
            self.gps_off_reason = "خدمة الموقع مطفأة — شغّلها من الإعدادات"

    def tick(self, *_):
        plugged = usb_plugged()
        fix = self.server.current_fix() if self.server else None
        link = bool(self.server and self.server.link_alive)
        was = bool(self.server and self.server.was_linked)
        gps_node, cable_node, pc_node = self.chain.nodes

        if fix:
            gps_node.show("ok", "محدد")
            self.coords.text = f"{fix['lat']:.6f} , {fix['lon']:.6f}"
            set_text(self.acc, f"الدقة ± {fix['acc']:.0f} م" if fix.get("acc") else "")
        else:
            gps_node.show("err" if self.gps_off_reason else "warn", "مطفأ" if self.gps_off_reason else "يبحث")
            self.coords.text = ""
            set_text(self.acc, self.gps_off_reason or "يبحث عن الأقمار… الأفضل قرب نافذة أو في الخارج")

        if plugged is None:
            cable_node.show("ok" if link else "idle", "موصول" if link else "؟")
        else:
            cable_node.show("ok" if plugged else "err", "موصول" if plugged else "مفصول")
        pc_node.show("ok" if link else ("err" if was else "idle"),
                     "متصل" if link else ("انقطع" if was else "بانتظار"))

        if link and fix:
            paint_headline(self.card, self.big, self.sub, "ok", "نجح الاتصال — يتم الإرسال",
                           "الكمبيوتر يستقبل موقعك الآن")
        elif link:
            paint_headline(self.card, self.big, self.sub, "warn", "متصل بالكمبيوتر", "بانتظار تحديد الموقع…")
        elif plugged is False or was:
            paint_headline(self.card, self.big, self.sub, "err", "انقطع الاتصال",
                           "الكابل مفصول — أعد توصيله بالكمبيوتر" if plugged is False
                           else "تأكد أن «مستقبل» يعمل على الكمبيوتر")
        else:
            paint_headline(self.card, self.big, self.sub, "idle", "بانتظار الكمبيوتر",
                           "وصّل الكابل وشغّل GPS Link على الكمبيوتر")

    def back(self):
        self.manager.current = "home"


def device_model():
    if not IS_ANDROID:
        return "Desktop"
    from jnius import autoclass
    build = autoclass("android.os.Build")
    return f"{build.MANUFACTURER} {build.MODEL}"


def usb_plugged():
    """True/False on Android (is a cable powering the phone?), None elsewhere."""
    if not IS_ANDROID:
        return None
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        intent_cls = autoclass("android.content.Intent")
        filt = autoclass("android.content.IntentFilter")(intent_cls.ACTION_BATTERY_CHANGED)
        bm = autoclass("android.os.BatteryManager")
        intent = activity.registerReceiver(None, filt)
        return intent is not None and intent.getIntExtra(bm.EXTRA_PLUGGED, 0) != 0
    except Exception:                                   # noqa: BLE001
        return None


# ================================================================= receiver (desktop)
#   state -> (headline level, title, next step, phone node, gps node)
STATE_UI = {
    "GETTING_ADB": ("warn", "تجهيز أول مرة…", "تنزيل أداة الاتصال بالهاتف (adb) — مرة واحدة فقط",
                    ("idle", "…"), ("idle", "…")),
    "NO_ADB": ("err", "تعذّر تنزيل أداة الاتصال", "تأكد من الإنترنت — سيعيد المحاولة تلقائياً",
               ("err", "—"), ("idle", "—")),
    "NO_PHONE": ("idle", "بانتظار توصيل الهاتف", "وصّل الهاتف بكابل USB",
                 ("idle", "غير موصول"), ("idle", "—")),
    "UNAUTHORIZED": ("warn", "الهاتف موصول — يحتاج إذن", "على الهاتف: اضغط «سماح» لتصحيح USB",
                     ("warn", "إذن"), ("idle", "—")),
    "WAITING_APP": ("warn", "تم التعرف على الهاتف", "افتح GPS Link على الهاتف",
                    ("ok", "موصول"), ("idle", "—")),
    "NO_FIX": ("warn", "نجح الاتصال بالهاتف", "الهاتف يبحث عن الأقمار…",
               ("ok", "متصل"), ("warn", "يبحث")),
    "CONNECTED": ("ok", "نجح الاتصال — الموقع يصل", "موقعك ظاهر في Google Earth",
                  ("ok", "متصل"), ("ok", "محدد")),
    "SIGNAL_LOST": ("err", "انقطع الاتصال / انقطعت الإشارة", "الكابل فُصل أو أُغلق التطبيق على الهاتف",
                    ("err", "انقطع"), ("err", "—")),
}


def launcher_dir():
    from gpslink.adbsetup import app_dir
    d = app_dir()
    return d if os.access(d, os.W_OK) else tempfile.gettempdir()


class ReceiverScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.rx = None
        self.kml = None
        self.serial_out = None
        self.ge_opened = False
        self.state = None
        root = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(12))
        root.add_widget(L("مستقبل", FS_H2, MUTED, size_hint_y=None, height=dp(30)))
        self.card, self.big, self.sub = headline_card()
        root.add_widget(self.card)
        self.chain = Chain(["الهاتف", "GPS", "Google Earth"], size_hint_y=None, height=dp(78))
        root.add_widget(self.chain)
        self.phone = L("", FS_BODY, size_hint_y=None, height=dp(26))
        self.coords = L("", FS_H2, size_hint_y=None, height=dp(30))
        root.add_widget(self.phone)
        root.add_widget(self.coords)

        self.open_btn = B("فتح في Google Earth", ACCENT, self.open_ge, FS_H2, size_hint_y=None, height=dp(56))
        root.add_widget(self.open_btn)

        follow_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6))
        self.follow = CheckBox(active=True, size_hint_x=None, width=dp(36))
        rtl(follow_row, L("الكاميرا تتبع موقعي", FS_BODY), self.follow)
        root.add_widget(follow_row)

        com_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self.com = Spinner(text="OFF", values=["OFF"] + list_com_ports(), font_size=sp(FS_BODY),
                           size_hint_x=0.4, background_normal="", background_color=IDLE)
        self.com.bind(text=lambda *_: self._apply_com())
        rtl(com_row, L("منفذ COM لزر Start (اختياري)", FS_SMALL, MUTED), self.com)
        root.add_widget(com_row)
        self.foot = L("", FS_SMALL, MUTED, size_hint_y=None, height=dp(22))
        root.add_widget(self.foot)
        self.add_widget(root)

    # -- lifecycle -------------------------------------------------------
    def on_enter(self):
        if self.rx:
            return
        from gpslink.adbsetup import ensure_adb
        from gpslink.kml import LiveKml
        from gpslink.receiver import Receiver
        self.kml = LiveKml()
        try:
            self.kml.start()
        except OSError:
            self.kml = None          # port busy (app already open?) — status still works
        self.rx = Receiver(on_state=lambda s, i: Clock.schedule_once(lambda *_: self.show(s, i)),
                           adb_provider=ensure_adb, outputs=[o for o in (self.kml,) if o])
        self.rx.start()
        self._apply_com()
        Clock.schedule_interval(self.refresh_ge_node, 1)
        self.show("NO_PHONE", {})

    def shutdown(self):
        if self.rx:
            self.rx.stop()
        if self.kml:
            self.kml.stop()

    def _apply_com(self):
        if not self.rx:
            return
        from gpslink.receiver import SerialNmeaOutput
        if self.serial_out in self.rx.outputs:
            self.rx.outputs.remove(self.serial_out)
            self.serial_out.close()
            self.serial_out = None
        if self.com.text != "OFF":
            self.serial_out = SerialNmeaOutput(self.com.text)
            self.rx.outputs.append(self.serial_out)

    # -- UI --------------------------------------------------------------
    def show(self, state, info):
        self.state = state
        level, title, hint, phone_n, gps_n = STATE_UI.get(state, ("idle", state, "", ("idle", ""), ("idle", "")))
        if state == "CONNECTED" and not self.ge_opened:
            hint = "اضغط «فتح في Google Earth»"
        paint_headline(self.card, self.big, self.sub, level, title, hint)
        self.chain.nodes[0].show(*phone_n)
        self.chain.nodes[1].show(*gps_n)
        model = info.get("model")
        set_text(self.phone, f"الهاتف: {model}" if model and state != "NO_PHONE" else "")
        fix = info.get("fix")
        if fix:
            acc = fix.get("acc")
            self.coords.text = f"{fix['lat']:.6f} , {fix['lon']:.6f}" + (f"   ±{acc:.0f} m" if acc else "")
            if not self.ge_opened:
                self.open_ge()                           # first fix: bring Google Earth up by itself
        elif state != "CONNECTED":
            self.coords.text = ""
        if state == "NO_ADB" and info.get("error"):
            self.foot.text = info["error"][:80]

    def refresh_ge_node(self, *_):
        node = self.chain.nodes[2]
        live = self.state == "CONNECTED"
        if self.kml and self.kml.requests and self.ge_opened:
            node.show("ok" if live else "err", "يعرض" if live else "آخر موقع")
        elif self.ge_opened:
            node.show("warn", "يفتح…")
        else:
            node.show("idle", "مغلق")
        extra = ""
        if self.serial_out:
            extra = f"  •  {self.serial_out.port}: " + ("خطأ" if self.serial_out.error else str(self.serial_out.writes))
        if self.state != "NO_ADB":
            set_text(self.foot, f"تحديثات Google Earth: {self.kml.requests if self.kml else 0}{extra}")

    def open_ge(self):
        if not self.kml:
            set_text(self.foot, "خادم KML غير متاح — هل البرنامج مفتوح مرتين؟")
            return
        from gpslink.kml import open_in_google_earth
        path = self.kml.write_launcher(launcher_dir(), follow=self.follow.active)
        if open_in_google_earth(path):
            self.ge_opened = True
        else:
            set_text(self.foot, f"لم أجد Google Earth Pro — افتح الملف يدوياً: {path}")


def list_com_ports():
    try:
        from serial.tools import list_ports
        return sorted(p.device for p in list_ports.comports())
    except Exception:                                   # noqa: BLE001
        return []


# ================================================================= app
class GpsLinkApp(App):
    title = "GPS Link"

    def build(self):
        Window.clearcolor = BG
        if not IS_ANDROID:
            Window.size = (480, 720)
        self.sm = ScreenManager(transition=NoTransition())
        self.sm.add_widget(Home(name="home"))
        self.sm.add_widget(SenderScreen(name="sender"))
        self.sm.add_widget(ReceiverScreen(name="receiver"))
        # each device has only one job, so open it directly
        Clock.schedule_once(lambda *_: setattr(self.sm, "current", "sender" if IS_ANDROID else "receiver"), 0)
        if "--smoke" in sys.argv:
            secs = float(sys.argv[sys.argv.index("--smoke") + 1])
            Clock.schedule_once(lambda *_: self._smoke_done(), secs)
        return self.sm

    def _smoke_done(self):
        print("SMOKE screen:", self.sm.current, "state:", self.sm.get_screen("receiver").state, flush=True)
        self.stop()

    def on_pause(self):
        return True          # keep GPS + server alive if the screen turns off

    def on_stop(self):
        self.sm.get_screen("receiver").shutdown()


if __name__ == "__main__":
    GpsLinkApp().run()
