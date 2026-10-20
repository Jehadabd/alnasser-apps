#!/usr/bin/env python3
"""
يبني موقع التطبيقات الثابت من مجلد apps/ إلى dist/.

لا خادم، لا قاعدة بيانات، لا نماذج، لا سكربتات خارجية: كل ما يُنشر ملفات ثابتة.
كل نص من ملفات JSON يُهرَّب قبل وضعه في الصفحة، وكل رابط تنزيل يُتحقق أنه
من إصدارات هذا المستودع نفسه على GitHub، والصور يُعاد ترميزها بلا بيانات
وصفية (EXIF/GPS).

الاستعمال:
    python tools/build.py                 # بناء عادي
    python tools/build.py --verify-assets # + تنزيل آخر ملف لكل منصة ومطابقة بصمته
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"
ASSETS = ROOT / "site" / "assets"
DIST = ROOT / "dist"

PLATFORMS = {
    "windows": {"label": "ويندوز", "exts": {".exe", ".msix", ".msi", ".zip"}},
    "android": {"label": "أندرويد", "exts": {".apk"}},
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}  # SVG ممنوع عمداً: قد يحوي سكربتاً
VIDEO_EXTS = {".mp4", ".webm"}
VIDEO_MAX = 90 * 1024 * 1024  # GitHub يرفض أي ملف فوق 100 ميغا
RE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
RE_VERSION = re.compile(r"^\d+(\.\d+){0,3}([-+][0-9A-Za-z.]{1,20})?$")
RE_SHA = re.compile(r"^[0-9a-f]{64}$")
RE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
RE_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
RE_PHONE = re.compile(r"^\+?[0-9]{6,15}$")

MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول",
          "تشرين الأول", "تشرين الثاني", "كانون الأول"]


class BuildError(Exception):
    pass


def e(s: object) -> str:
    """تهريب HTML لكل نص يدخل الصفحة."""
    return html.escape(str(s), quote=True)


def fail(msg: str) -> None:
    raise BuildError(msg)


def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as ex:
        fail(f"{p.relative_to(ROOT)}: JSON غير صالح ({ex})")
    return {}


def ar_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return e(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def human_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} غيغابايت"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} ميغابايت"
    return f"{max(1, n // 1024)} كيلوبايت"


def vkey(v: str) -> tuple:
    core = re.split(r"[-+]", v, maxsplit=1)[0]
    return tuple(int(x) for x in core.split(".")) + ((1,) if "-" not in v else (0,))


@dataclass
class Release:
    platform: str
    version: str
    file: str
    size: int
    sha256: str
    url: str
    date: str
    notes: list[str] = field(default_factory=list)


@dataclass
class App:
    id: str
    name: str
    tagline: str
    description: list[str]
    features: list[dict]
    requirements: dict
    order: int
    icon: str | None
    shots: list[dict]
    releases: list[Release]
    videos: list[dict] = field(default_factory=list)
    icon_path: str | None = None

    def latest(self, platform: str) -> Release | None:
        rs = [r for r in self.releases if r.platform == platform]
        return max(rs, key=lambda r: vkey(r.version)) if rs else None

    @property
    def platforms(self) -> list[str]:
        return [p for p in PLATFORMS if self.latest(p)]


# ───────────────────────────── القراءة والتحقق ─────────────────────────────

def text_list(v, where: str, limit: int = 40) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        fail(f"{where}: يجب أن يكون نصاً أو قائمة نصوص")
    if len(v) > limit:
        fail(f"{where}: أكثر من {limit} عنصراً")
    return [x.strip() for x in v if x.strip()]


def load_releases(app_dir: Path, app_id: str, repo: str) -> list[Release]:
    out: list[Release] = []
    rdir = app_dir / "releases"
    if not rdir.is_dir():
        return out
    prefix = f"https://github.com/{repo}/releases/download/"
    seen = set()
    for p in sorted(rdir.glob("*.json")):
        d = load_json(p)
        where = f"{p.relative_to(ROOT)}"
        plat = d.get("platform")
        if plat not in PLATFORMS:
            fail(f"{where}: منصة غير معروفة «{plat}»")
        ver = str(d.get("version", ""))
        if not RE_VERSION.match(ver):
            fail(f"{where}: رقم إصدار غير صالح «{ver}»")
        fname = str(d.get("file", ""))
        if not RE_FILE.match(fname) or Path(fname).suffix.lower() not in PLATFORMS[plat]["exts"]:
            fail(f"{where}: اسم ملف غير مقبول «{fname}» لمنصة {plat}")
        sha = str(d.get("sha256", "")).lower()
        if not RE_SHA.match(sha):
            fail(f"{where}: بصمة SHA-256 غير صالحة")
        url = str(d.get("url", ""))
        if not url.startswith(prefix) or not url.endswith("/" + fname) or any(c in url for c in "\"'<> "):
            fail(f"{where}: رابط التنزيل ليس من إصدارات {repo}: {url}")
        size = d.get("size")
        if not isinstance(size, int) or size <= 0:
            fail(f"{where}: حجم الملف غير صالح")
        key = (plat, ver)
        if key in seen:
            fail(f"{where}: الإصدار {ver} لمنصة {plat} مكرر")
        seen.add(key)
        out.append(Release(plat, ver, fname, size, sha, url, str(d.get("date", ""))[:10],
                           text_list(d.get("notes"), where + " notes")))
    return out


def load_app(app_dir: Path, repo: str) -> App:
    app_id = app_dir.name
    if not RE_ID.match(app_id):
        fail(f"apps/{app_id}: اسم المجلد يجب أن يكون حروفاً إنجليزية صغيرة وأرقاماً وشرطة")
    d = load_json(app_dir / "app.json")
    where = f"apps/{app_id}/app.json"
    name = str(d.get("name", "")).strip()
    if not name:
        fail(f"{where}: name مطلوب")
    feats = []
    for i, f in enumerate(d.get("features", []) or []):
        if isinstance(f, str):
            f = {"title": f}
        if not isinstance(f, dict) or not str(f.get("title", "")).strip():
            fail(f"{where}: الميزة رقم {i + 1} بلا عنوان")
        feats.append({"title": str(f["title"]).strip(), "text": str(f.get("text", "")).strip()})
    reqs = d.get("requirements", {}) or {}
    if not isinstance(reqs, dict):
        fail(f"{where}: requirements يجب أن يكون كائناً")
    icon = None
    for ext in (".png", ".webp", ".jpg"):
        if (app_dir / f"icon{ext}").is_file():
            icon = f"icon{ext}"
            break
    captions = d.get("screenshots", {}) or {}
    if not isinstance(captions, dict):
        fail(f"{where}: screenshots يجب أن يكون {{\"اسم الملف\": \"الوصف\"}}")
    shots = []
    sdir = app_dir / "screenshots"
    if sdir.is_dir():
        for p in sorted(sdir.iterdir()):
            if p.name.startswith("."):
                continue
            if p.suffix.lower() not in IMAGE_EXTS:
                fail(f"apps/{app_id}/screenshots/{p.name}: نوع صورة غير مقبول (PNG أو JPG أو WEBP فقط)")
            shots.append({"src": p, "caption": str(captions.get(p.name, "")).strip()})
    vcaps = d.get("videos", {}) or {}
    if not isinstance(vcaps, dict):
        fail(f"{where}: videos يجب أن يكون {{\"اسم الملف\": \"الوصف\"}}")
    videos = []
    vdir = app_dir / "videos"
    if vdir.is_dir():
        for p in sorted(vdir.iterdir()):
            if p.name.startswith("."):
                continue
            if p.suffix.lower() not in VIDEO_EXTS or not RE_FILE.match(p.name):
                fail(f"apps/{app_id}/videos/{p.name}: فيديو غير مقبول (MP4 أو WEBM، واسم بحروف إنجليزية)")
            if p.stat().st_size > VIDEO_MAX:
                fail(f"apps/{app_id}/videos/{p.name}: أكبر من 90 ميغا — اضغطه أولاً")
            head = p.read_bytes()[:12]
            ok = head[4:8] == b"ftyp" if p.suffix.lower() == ".mp4" else head[:4] == b"\x1a\x45\xdf\xa3"
            if not ok:
                fail(f"apps/{app_id}/videos/{p.name}: الملف ليس فيديو {p.suffix} حقيقياً")
            # windows-… يظهر في خطوات ويندوز، android-… في خطوات أندرويد، غيرهما فيديو عام
            plat = next((k for k in PLATFORMS if p.name.lower().startswith(k + "-")), "")
            videos.append({"src": p, "caption": str(vcaps.get(p.name, "")).strip(), "platform": plat})
    return App(
        videos=videos,
        id=app_id,
        name=name,
        tagline=str(d.get("tagline", "")).strip(),
        description=text_list(d.get("description"), where + " description"),
        features=feats,
        requirements={k: str(v) for k, v in reqs.items() if k in PLATFORMS},
        order=int(d.get("order", 100)),
        icon=icon,
        shots=shots,
        releases=load_releases(app_dir, app_id, repo),
    )


# ───────────────────────────── الصور ─────────────────────────────

def process_image(src: Path, dst_dir: Path, stem: str, max_w: int) -> tuple[str, int, int]:
    """يعيد ترميز الصورة (بلا EXIF/GPS) إلى WEBP بعرض أقصى. يعيد (الاسم، العرض، الارتفاع)."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 60_000_000
    try:
        with Image.open(src) as im:
            im.verify()
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            if im.width > max_w:
                im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
            dst_dir.mkdir(parents=True, exist_ok=True)
            name = f"{stem}.webp"
            im.save(dst_dir / name, "WEBP", quality=86, method=6)  # بلا exif=… ⇒ تُحذف البيانات الوصفية
            return name, im.width, im.height
    except Exception as ex:  # noqa: BLE001
        fail(f"{src.relative_to(ROOT)}: صورة تالفة أو غير مدعومة ({ex})")
    return "", 0, 0


# ───────────────────────────── القوالب ─────────────────────────────

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; media-src 'self'; "
       "base-uri 'none'; form-action 'none'; upgrade-insecure-requests")


def page(title: str, body: str, depth: int, site: dict, desc: str = "") -> str:
    up = "../" * depth
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="light">
<title>{e(title)}</title>
<meta name="description" content="{e(desc or site['about'])}">
<link rel="stylesheet" href="{up}assets/style.css">
<link rel="icon" href="{up}assets/favicon.png">
<script src="{up}assets/site.js" defer></script>
</head>
<body>
<a class="skip" href="#main">تخطَّ إلى المحتوى</a>
<header class="top">
  <div class="wrap top-in">
    <a class="brand" href="{up}index.html">{e(site['title'])}</a>
    {contact_links(site, "top-contact")}
  </div>
</header>
<main id="main">
{body}
</main>
<footer class="foot">
  <div class="wrap foot-in">
    <p>{e(site['owner'])} — هذا هو الموقع الرسمي الوحيد لتنزيل تطبيقاتنا. لا ترسل لأحد كلمة سر أو رمز تحقق باسمنا.</p>
    <p><a href="{up}security.html">كيف نحمي ملفات التنزيل</a></p>
  </div>
</footer>
</body>
</html>
"""


def contact_links(site: dict, cls: str) -> str:
    c = site.get("contact", {})
    links = []
    wa = c.get("whatsapp", "")
    if wa:
        links.append(f'<a href="https://wa.me/{e(wa.lstrip("+"))}" rel="noopener noreferrer">واتساب</a>')
    ph = c.get("phone", "")
    if ph:
        links.append(f'<a href="tel:{e(ph)}" dir="ltr">{e(ph)}</a>')
    return f'<nav class="{cls}" aria-label="تواصل">{"".join(links)}</nav>' if links else ""


def shot_frame(img: dict | None, up: str, cls: str = "") -> str:
    if not img:
        return ""
    return (f'<figure class="frame {cls}"><span class="frame-bar" aria-hidden="true"><i></i><i></i><i></i></span>'
            f'<img src="{up}{e(img["path"])}" width="{img["w"]}" height="{img["h"]}" alt="{e(img["caption"] or "لقطة من التطبيق")}"'
            f' loading="eager"></figure>')


def download_button(app: App, plat: str, up: str) -> str:
    r = app.latest(plat)
    if not r:
        return ""
    return (f'<a class="btn btn-{plat}" href="{e(r.url)}" data-platform="{plat}" rel="noopener noreferrer">'
            f'تنزيل ل{PLATFORMS[plat]["label"]}<small>الإصدار {e(r.version)} — {human_size(r.size)}</small></a>')


def voucher(app: App, plat: str) -> str:
    """«سند التنزيل»: كل ما يلزم للتأكد أن الملف أصلي."""
    r = app.latest(plat)
    if not r:
        return ""
    req = app.requirements.get(plat, "")
    notes = "".join(f"<li>{e(n)}</li>" for n in r.notes)
    return f"""
<section class="voucher" data-platform="{plat}" aria-labelledby="v-{plat}">
  <div class="voucher-head">
    <h3 id="v-{plat}">نسخة {PLATFORMS[plat]['label']}</h3>
    <span class="seal" aria-hidden="true">أصلي</span>
  </div>
  <dl class="voucher-rows">
    <div><dt>الإصدار</dt><dd dir="ltr">{e(r.version)}</dd></div>
    <div><dt>تاريخ النشر</dt><dd>{ar_date(r.date)}</dd></div>
    <div><dt>الحجم</dt><dd>{human_size(r.size)}</dd></div>
    <div><dt>اسم الملف</dt><dd dir="ltr">{e(r.file)}</dd></div>
    {f'<div><dt>يعمل على</dt><dd>{e(req)}</dd></div>' if req else ''}
  </dl>
  <div class="hash">
    <span class="hash-label">بصمة الملف (SHA-256)</span>
    <code dir="ltr" id="h-{plat}">{e(r.sha256)}</code>
    <button type="button" class="copy" data-copy="h-{plat}" hidden>نسخ البصمة</button>
  </div>
  {f'<ul class="voucher-notes" aria-label="الجديد في هذا الإصدار">{notes}</ul>' if notes else ''}
  <a class="btn btn-{plat} voucher-btn" href="{e(r.url)}" rel="noopener noreferrer">تنزيل الملف</a>
</section>"""


def verify_steps(app: App) -> str:
    parts = []
    if app.latest("windows"):
        r = app.latest("windows")
        parts.append(f"""
<div class="verify-block">
  <h3>على ويندوز</h3>
  <ol>
    <li>افتح مجلد التنزيلات، واضغط بالزر الأيمن في مكان فارغ ثم «فتح في Terminal» (أو PowerShell).</li>
    <li>اكتب الأمر التالي واضغط Enter:<pre dir="ltr"><code>Get-FileHash .\\{e(r.file)} -Algorithm SHA256</code></pre></li>
    <li>قارن الرقم الذي يظهر بالبصمة أعلاه. إن اختلف حرف واحد فلا تشغّل الملف واحذفه.</li>
  </ol>
</div>""")
    if app.latest("android"):
        parts.append("""
<div class="verify-block">
  <h3>على أندرويد</h3>
  <ol>
    <li>نزّل الملف من هذه الصفحة فقط، لا من رابط أُرسل إليك في مجموعة أو من شخص لا تعرفه.</li>
    <li>عند التثبيت سيطلب الهاتف السماح بالتثبيت من «مصادر غير معروفة» للمتصفح: اسمح به لهذا التثبيت ثم أعد إغلاقه من الإعدادات.</li>
    <li>إن ظهر لك أن التطبيق «مثبّت بتوقيع مختلف» عند التحديث فلا تكمل وتواصل معنا: هذا يعني أن الملف ليس منا.</li>
  </ol>
</div>""")
    return "".join(parts)


def video_tag(v: dict, up: str) -> str:
    cap = f'<figcaption>{e(v["caption"])}</figcaption>' if v["caption"] else ""
    return (f'<figure class="video"><video controls preload="metadata" playsinline src="{up}{e(v["path"])}">'
            f'متصفحك لا يعرض الفيديو — <a href="{up}{e(v["path"])}">افتحه مباشرة</a></video>{cap}</figure>')


def install_steps(app: App, up: str) -> str:
    """خطوات التثبيت لكل منصة، مع فيديو المنصة إن وُجد (windows-… / android-…)."""
    parts = []
    w = app.latest("windows")
    if w:
        vids = "".join(video_tag(v, up) for v in app.videos if v["platform"] == "windows")
        is_zip = w.file.lower().endswith(".zip")
        parts.append(f"""
<div class="verify-block install-block">
  <h3>على ويندوز</h3>
  {vids}
  <ol>
    <li>اضغط زر «تنزيل لويندوز» أعلاه{"، فيُنزَّل ملف مضغوط (zip)" if is_zip else ""}.</li>
    {"" if is_zip else "<li>قد يحذّرك المتصفح (Edge) أن الملف «غير شائع». مرّر الفأرة على التحذير في قائمة التنزيلات، واضغط <b>⋯</b> ثم <b>الاحتفاظ (Keep)</b>. إن ظهرت رسالة ثانية فاضغط <b>إظهار المزيد</b> ثم <b>الاحتفاظ على أي حال</b>.</li>"}
    {f'<li>افتح مجلد التنزيلات، واضغط بالزر الأيمن على <code dir="ltr">{e(w.file)}</code> ثم <b>استخراج الكل (Extract All)</b> ثم <b>استخراج</b>. يُفتح مجلد فيه البرنامج وملف «طريقة التثبيت».</li><li>افتح ملف البرنامج <code dir="ltr">{e(w.file[:-4])}.exe</code> داخل المجلد. لا تشغّله من داخل الملف المضغوط دون استخراج.</li>' if is_zip else f'<li>افتح الملف <code dir="ltr">{e(w.file)}</code> من مجلد التنزيلات.</li>'}
    <li>ستظهر شاشة زرقاء «Windows protected your PC». اضغط <b>مزيد من المعلومات (More info)</b> ثم <b>التشغيل على أي حال (Run anyway)</b>.</li>
    <li>اضغط <b>نعم</b> عند سؤال الصلاحيات، ثم <b>التالي</b> حتى <b>تثبيت</b> ثم <b>إنهاء</b>.</li>
    <li>شغّل البرنامج من سطح المكتب، وسجّل الدخول باسم حساب الترخيص وكلمة سره.</li>
  </ol>
</div>""")
    a = app.latest("android")
    if a:
        vids = "".join(video_tag(v, up) for v in app.videos if v["platform"] == "android")
        parts.append(f"""
<div class="verify-block install-block">
  <h3>على أندرويد</h3>
  {vids}
  <ol>
    <li>افتح هذه الصفحة من متصفح الهاتف (Chrome) واضغط «تنزيل لأندرويد». الحجم {human_size(a.size)}، فالأفضل على الواي فاي.</li>
    <li>إن قال المتصفح إن الملف «قد يكون ضاراً» فاضغط <b>تنزيل على أي حال</b>.</li>
    <li>افتح الملف من الإشعار أو من «التنزيلات».</li>
    <li>أول مرة يقول الهاتف إن التثبيت من هذا المصدر غير مسموح: اضغط <b>الإعدادات</b>، وفعّل <b>السماح من هذا المصدر</b>، ثم ارجع.</li>
    <li>اضغط <b>تثبيت</b>. إن ظهر تحذير Play Protect فاختر <b>مزيد من التفاصيل</b> ثم <b>التثبيت على أي حال</b>.</li>
    <li>افتح التطبيق وسجّل الدخول باسم حساب الترخيص وكلمة سره.</li>
  </ol>
</div>""")
    return "".join(parts)


def app_page(app: App, site: dict, imgs: list[dict]) -> str:
    up = "../../"
    hero_img = imgs[0] if imgs else None
    buttons = "".join(download_button(app, p, up) for p in app.platforms)
    desc = "".join(f"<p>{e(p)}</p>" for p in app.description)
    def feat(f: dict) -> str:
        extra = "<span>" + e(f["text"]) + "</span>" if f["text"] else ""
        return "<li><strong>" + e(f["title"]) + "</strong>" + extra + "</li>"

    def shot(i: dict) -> str:
        alt = e(i["caption"] or "لقطة من التطبيق")
        cap = "<figcaption>" + e(i["caption"]) + "</figcaption>" if i["caption"] else ""
        src = up + e(i["path"])
        return (f'<figure class="shot"><a href="{src}"><img src="{src}" width="{i["w"]}" height="{i["h"]}"'
                f' alt="{alt}" loading="lazy"></a>{cap}</figure>')

    feats = "".join(feat(f) for f in app.features)
    gallery = "".join(shot(i) for i in imgs)
    older_rows = []
    for r in sorted(app.releases, key=lambda r: vkey(r.version), reverse=True):
        if app.latest(r.platform) is r:
            continue
        older_rows.append(
            f'<tr><td>{PLATFORMS[r.platform]["label"]}</td><td dir="ltr">{e(r.version)}</td><td>{ar_date(r.date)}</td>'
            f'<td><code dir="ltr" class="short-hash" title="{e(r.sha256)}">{e(r.sha256[:16])}…</code></td>'
            f'<td><a href="{e(r.url)}" rel="noopener noreferrer">تنزيل</a></td></tr>')
    no_release = '<p class="empty">لم يُنشر ملف لهذا التطبيق بعد. تواصل معنا لنرسل لك موعد النشر.</p>'
    body = f"""
<section class="hero">
  <div class="wrap hero-in">
    <div class="hero-text">
      {f'<img class="app-icon" src="{up}{e(app.icon_path)}" width="72" height="72" alt="">' if app.icon_path else ''}
      <h1 class="display">{e(app.name)}</h1>
      {f'<p class="lede">{e(app.tagline)}</p>' if app.tagline else ''}
      <div class="btns">{buttons or no_release}</div>
      {'<p class="how"><a href="#install">كيف أثبّت البرنامج؟</a></p>' if app.platforms else ''}
    </div>
    {shot_frame(hero_img, up, "hero-shot")}
  </div>
</section>
{f'<section class="wrap block"><h2>لقطات من التطبيق</h2><div class="gallery">{gallery}</div></section>' if len(imgs) > 1 else ''}
{f'<section class="wrap block prose">{desc}</section>' if desc else ''}
{f'<section class="wrap block"><h2>ماذا يقدّم</h2><ul class="features">{feats}</ul></section>' if feats else ''}
{f'''<section class="wrap block" id="download"><h2>التنزيل</h2>
<p class="muted">كل ملف هنا رُفع منا مباشرة، ومعه بصمته. البصمة تتغيّر كلياً لو تغيّر بايت واحد في الملف.</p>
<div class="vouchers">{"".join(voucher(app, p) for p in app.platforms)}</div></section>''' if app.platforms else ''}
{f'''<section class="wrap block" id="install"><h2>كيف أثبّت البرنامج؟</h2>
<p class="muted">ستظهر لك تحذيرات من ويندوز أو الهاتف: هذا متوقع لأي برنامج يُنزَّل من خارج المتجر، وليس لأن في الملف شيئاً. لكن نزّله من هذه الصفحة فقط، وطابق بصمته (الخطوات أسفل).</p>
{"".join(video_tag(v, up) for v in app.videos if not v["platform"])}
<div class="verify">{install_steps(app, up)}</div></section>''' if app.platforms else ''}
{f'<section class="wrap block"><h2>تأكّد أن الملف أصلي</h2><div class="verify">{verify_steps(app)}</div></section>' if app.platforms else ''}
{f'<section class="wrap block"><h2>إصدارات سابقة</h2><div class="table-wrap"><table class="older"><thead><tr><th>المنصة</th><th>الإصدار</th><th>التاريخ</th><th>البصمة</th><th></th></tr></thead><tbody>{"".join(older_rows)}</tbody></table></div></section>' if older_rows else ''}
"""
    return page(f"{app.name} — {site['title']}", body, 2, site, app.tagline)


def index_page(apps: list[App], site: dict, cover: dict[str, dict | None]) -> str:
    up = ""
    if not apps:
        body = '<section class="wrap block"><p class="empty">لا تطبيقات منشورة بعد.</p></section>'
        return page(site["title"], body, 0, site)
    first, rest = apps[0], apps[1:]
    buttons = "".join(download_button(first, p, up) for p in first.platforms)
    hero = f"""
<section class="hero">
  <div class="wrap hero-in">
    <div class="hero-text">
      <p class="kicker">{e(site['about'])}</p>
      <h1 class="display">{e(first.name)}</h1>
      {f'<p class="lede">{e(first.tagline)}</p>' if first.tagline else ''}
      <div class="btns">{buttons}<a class="btn btn-quiet" href="apps/{e(first.id)}/index.html">التفاصيل واللقطات</a></div>
    </div>
    {shot_frame(cover.get(first.id), up, "hero-shot")}
  </div>
</section>"""
    rows = ""
    for a in rest:
        img = cover.get(a.id)
        plats = "، ".join(PLATFORMS[p]["label"] for p in a.platforms) or "قريباً"
        rows += f"""
<li class="app-row">
  <a href="apps/{e(a.id)}/index.html">
    {f'<img src="{e(img["path"])}" width="{img["w"]}" height="{img["h"]}" alt="" loading="lazy">' if img else '<span class="noimg" aria-hidden="true"></span>'}
    <span class="app-row-text"><strong>{e(a.name)}</strong>{f'<span>{e(a.tagline)}</span>' if a.tagline else ''}<em>{plats}</em></span>
  </a>
</li>"""
    others = f'<section class="wrap block"><h2>تطبيقات أخرى</h2><ul class="app-list">{rows}</ul></section>' if rest else ""
    return page(site["title"], hero + others, 0, site)


def security_page(site: dict, repo: str) -> str:
    body = f"""
<section class="wrap block prose narrow">
<h1 class="display small">كيف نحمي ملفات التنزيل</h1>
<p>هذا الموقع صفحات ثابتة فقط: لا حسابات، ولا كلمات سر، ولا نماذج تُرسل بياناتك، ولا إعلانات ولا أدوات تتبّع. لا نجمع منك أي معلومة.</p>
<p>ملفات التطبيقات محفوظة في صفحة الإصدارات على GitHub، ولكل ملف بصمة SHA-256 منشورة بجانبه. البصمة تُحسب على حاسبتنا قبل الرفع، ويتحقق الموقع منها مرة أخرى عند كل نشر.</p>
<p>إن وصلك ملف لتطبيقاتنا من أي مكان آخر، أو طلب منك أحد كلمة سر أو رمز تحقق باسمنا، فلا تثق به وتواصل معنا.</p>
<p>وجدت مشكلة أمنية؟ راسلنا عبر واتساب أو الهاتف في أعلى الصفحة.</p>
<p class="muted" dir="ltr">github.com/{e(repo)}</p>
</section>"""
    return page(f"الأمان — {site['title']}", body, 0, site)


# ───────────────────────────── البناء ─────────────────────────────

def verify_assets(apps: list[App]) -> None:
    for a in apps:
        for p in a.platforms:
            r = a.latest(p)
            print(f"  ⇣ مطابقة بصمة {a.id} {p} {r.version} …", flush=True)
            h = hashlib.sha256()
            req = urllib.request.Request(r.url, headers={"User-Agent": "site-verify"})
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 (رابط مُتحقَّق منه أعلاه)
                while chunk := resp.read(1 << 20):
                    h.update(chunk)
            if h.hexdigest() != r.sha256:
                fail(f"بصمة الملف المرفوع لا تطابق المنشور: {a.id} {p} {r.version}\n"
                     f"  المتوقع {r.sha256}\n  الفعلي  {h.hexdigest()}\n"
                     "لا يُنشر الموقع: قد يكون الملف استُبدل بعد الرفع.")


def build(verify: bool) -> None:
    site = load_json(ROOT / "site.json")
    repo = os.environ.get("GITHUB_REPOSITORY") or site.get("repo", "")
    if not RE_REPO.match(repo):
        fail("site.json: ضع repo بصيغة owner/name (أو يُؤخذ تلقائياً على GitHub)")
    site.setdefault("title", "تطبيقاتنا")
    site.setdefault("owner", "")
    site.setdefault("about", "")
    c = site.get("contact", {}) or {}
    for k in ("whatsapp", "phone"):
        if c.get(k) and not RE_PHONE.match(str(c[k])):
            fail(f"site.json: contact.{k} يجب أن يكون أرقاماً فقط مع + اختيارية")
    site["contact"] = c

    apps = [load_app(d, repo) for d in sorted(APPS.iterdir()) if d.is_dir() and not d.name.startswith((".", "_"))]
    apps.sort(key=lambda a: (a.order, a.name))
    if verify:
        verify_assets(apps)

    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(ASSETS, DIST / "assets")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    cover: dict[str, dict | None] = {}
    for a in apps:
        out = DIST / "apps" / a.id
        imgs = []
        for i, s in enumerate(a.shots):
            name, w, h = process_image(s["src"], out / "shots", f"{i + 1:02d}", 1600)
            imgs.append({"path": f"apps/{a.id}/shots/{name}", "w": w, "h": h, "caption": s["caption"]})
        for v in a.videos:
            (out / "videos").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(v["src"], out / "videos" / v["src"].name)
            v["path"] = f"apps/{a.id}/videos/{v['src'].name}"
        if a.icon:
            name, _, _ = process_image(APPS / a.id / a.icon, out, "icon", 256)
            a.icon_path = f"apps/{a.id}/{name}"
        cover[a.id] = imgs[0] if imgs else None
        (out / "index.html").write_text(app_page(a, site, imgs), encoding="utf-8")

    (DIST / "index.html").write_text(index_page(apps, site, cover), encoding="utf-8")
    (DIST / "security.html").write_text(security_page(site, repo), encoding="utf-8")
    # صفحة 404 تُعرض على أي عمق، فروابطها مطلقة من جذر الموقع (/<اسم المستودع>/ على github.io)
    base = site.get("base_path") or ("/" if repo.split("/")[1].endswith(".github.io") else f"/{repo.split('/')[1]}/")
    nf = page("الصفحة غير موجودة", '<section class="wrap block"><h1 class="display small">الصفحة غير موجودة</h1>'
              '<p><a href="index.html">ارجع إلى الصفحة الرئيسية</a></p></section>', 0, site)
    for attr in ('href="', 'src="'):
        for rel in ("assets/", "index.html", "security.html"):
            nf = nf.replace(attr + rel, attr + base + rel)
    (DIST / "404.html").write_text(nf, encoding="utf-8")
    (DIST / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    wk = DIST / ".well-known"
    wk.mkdir()
    contact = c.get("whatsapp") and f"https://wa.me/{c['whatsapp'].lstrip('+')}" or f"https://github.com/{repo}/issues"
    (wk / "security.txt").write_text(
        f"Contact: {contact}\nExpires: {date.today().replace(year=date.today().year + 1).isoformat()}T00:00:00Z\n"
        "Preferred-Languages: ar, en\n", encoding="utf-8")

    n_rel = sum(len(a.releases) for a in apps)
    print(f"✓ بُني الموقع: {len(apps)} تطبيق، {n_rel} إصدار ← {DIST.relative_to(ROOT)}/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-assets", action="store_true", help="نزّل آخر ملف لكل منصة وطابق بصمته")
    args = ap.parse_args()
    try:
        build(args.verify_assets)
    except BuildError as ex:
        print(f"✗ {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
