import os, io, json, html, random, asyncio, logging
from datetime import datetime, timezone
from urllib.parse import quote
from aiohttp import web
from telethon import TelegramClient, events, utils, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.types import MessageEntityBold, MessageEntitySpoiler

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ.get("SESSION", "").strip()
ADMIN_KEY = os.environ.get("ADMIN_KEY", "change-me")
PORT = int(os.environ.get("PORT", "10000"))
STARTUP_DELAY = int(os.environ.get("STARTUP_DELAY", "20"))
SETTINGS_FILE = "ccbot_settings.json"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ccbot")

S = {"enabled": True, "fmt": {"bold": False, "spoiler": False}, "channels": {}, "packs": {"default": ["🔥", "عالیه 👌", "ممنون از پست"]}}
client = None
login_state = {}

HELP = """دستورها (داخل Saved Messages بفرست):
/status — وضعیت
/addch @channel یا -100123... — افزودن کانال
/delch کانال — حذف کانال
/delay کانال|all ثانیه یا بازه مثل 30-50 — تأخیر کامنت
/fmt bold on|off — بولد کردن کامنت
/fmt spoiler on|off — اسپویلر کردن کامنت
/addtext نام_پک
متن چندخطی — افزودن یک متن کامل (چندخطی) به پک
/setpack کانال|all نام_پک — انتخاب پک متن
/packs — لیست پک‌ها
/pack نام
متن۱
متن۲ — ساخت پک / افزودن متن (هر متن یک خط)
/deltext نام شماره — حذف یک متن
/delpack نام — حذف پک
/pause و /resume — توقف و ادامه"""


# ---------- ذخیره تنظیمات داخل Saved Messages ----------
def is_settings_msg(m):
    return bool(m.document and m.file and m.file.name == SETTINGS_FILE)


async def load_settings():
    global S
    async for m in client.iter_messages("me", limit=100):
        if is_settings_msg(m):
            data = await m.download_media(file=bytes)
            S = json.loads(data.decode("utf-8"))
            log.info("settings loaded")
            return


async def save_settings():
    old = [m.id async for m in client.iter_messages("me", limit=100) if is_settings_msg(m)]
    buf = io.BytesIO(json.dumps(S, ensure_ascii=False, indent=1).encode("utf-8"))
    buf.name = SETTINGS_FILE
    await client.send_file("me", buf, force_document=True, caption="⚙️ تنظیمات ربات (پاک نکن)")
    if old:
        await client.delete_messages("me", old)


# ---------- کمکی‌ها ----------
async def resolve(arg):
    arg = arg.strip()
    if arg.lstrip("-").isdigit():
        n = int(arg)
        if n > 0:
            n = int("-100" + str(n))
        return await client.get_entity(n)
    return await client.get_entity(arg)


def find_ch(arg):
    a = arg.strip().lower().lstrip("@")
    if a == "all":
        return list(S["channels"])
    out = []
    for k, c in S["channels"].items():
        if a in (k, k.replace("-100", ""), c.get("username", "").lower(), c["title"].lower()):
            out.append(k)
    return out


async def join_discussion(ent):
    try:
        full = await client(GetFullChannelRequest(ent))
        linked = full.full_chat.linked_chat_id
        if not linked:
            return "⚠️ این کانال گروه کامنت ندارد"
        for c in full.chats:
            if c.id == linked:
                try:
                    await client(JoinChannelRequest(c))
                except Exception as e:
                    log.info("join: %s", e)
                return "✅ عضو گروه کامنت شد"
    except Exception as e:
        return f"⚠️ خطا در عضویت گروه کامنت: {e}"
    return ""


# ---------- کامنت گذاشتن ----------
def delay_range(cfg):
    d = cfg.get("delay", 7)
    return (d[0], d[1]) if isinstance(d, list) else (d, d)


def make_entities(text):
    f = S.get("fmt", {})
    n = len(text.encode("utf-16-le")) // 2
    ents = []
    if f.get("bold"):
        ents.append(MessageEntityBold(0, n))
    if f.get("spoiler"):
        ents.append(MessageEntitySpoiler(0, n))
    return ents or None


async def comment(chat_id, msg, cfg):
    elapsed = (datetime.now(timezone.utc) - msg.date).total_seconds()
    lo, hi = delay_range(cfg)
    await asyncio.sleep(max(0, random.uniform(lo, hi) - elapsed))
    texts = S["packs"].get(cfg.get("pack", "default")) or []
    if not texts:
        return
    for _ in range(3):
        try:
            text = random.choice(texts)
            await client.send_message(chat_id, text, comment_to=msg.id,
                                      formatting_entities=make_entities(text))
            return
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            log.exception("comment failed")
            await client.send_message("me", f"⚠️ خطا در {cfg.get('title')}: {e}")
            return


async def on_post(event):
    if not S.get("enabled") or not event.is_channel or event.is_group:
        return
    cfg = S["channels"].get(str(event.chat_id))
    if cfg:
        asyncio.create_task(comment(event.chat_id, event.message, cfg))


# ---------- پنل دستورها ----------
async def on_cmd(event):
    t = (event.raw_text or "").strip()
    if not t.startswith("/"):
        return
    parts = t.split(None, 1)
    cmd = parts[0][1:].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    try:
        if cmd in ("help", "start"):
            await event.respond(HELP)

        elif cmd == "status":
            lines = ["🟢 روشن" if S["enabled"] else "⏸ متوقف"]
            f = S.get("fmt", {})
            lines.append(f"قالب: بولد={'✅' if f.get('bold') else '❌'} اسپویلر={'✅' if f.get('spoiler') else '❌'}")
            for k, c in S["channels"].items():
                lo, hi = delay_range(c)
                d = f"{lo:g}s" if lo == hi else f"{lo:g}-{hi:g}s"
                lines.append(f"• {c['title']} ({k}) | {d} | پک: {c['pack']}")
            if not S["channels"]:
                lines.append("هنوز کانالی ثبت نشده")
            lines.append("پک‌ها: " + ", ".join(f"{n}({len(v)})" for n, v in S["packs"].items()))
            await event.respond("\n".join(lines))

        elif cmd == "addch":
            ent = await resolve(rest)
            if not getattr(ent, "broadcast", False):
                await event.respond("❌ این یک کانال نیست")
                return
            key = str(utils.get_peer_id(ent))
            S["channels"][key] = {"title": ent.title, "username": ent.username or "", "delay": 7, "pack": "default"}
            await save_settings()
            note = await join_discussion(ent)
            await event.respond(f"✅ اضافه شد: {ent.title}\n{note}")

        elif cmd == "delch":
            keys = find_ch(rest)
            for k in keys:
                S["channels"].pop(k, None)
            await save_settings()
            await event.respond(f"🗑 حذف شد: {len(keys)} کانال")

        elif cmd == "delay":
            target, val = rest.rsplit(None, 1)
            keys = find_ch(target)
            if "-" in val:
                lo, hi = [float(x) for x in val.split("-")]
                d = [lo, hi]
            else:
                d = float(val)
            for k in keys:
                S["channels"][k]["delay"] = d
            await save_settings()
            await event.respond(f"⏱ تأخیر {val} ثانیه برای {len(keys)} کانال")

        elif cmd == "setpack":
            target, name = rest.rsplit(None, 1)
            if name not in S["packs"]:
                await event.respond("❌ این پک وجود ندارد")
                return
            keys = find_ch(target)
            for k in keys:
                S["channels"][k]["pack"] = name
            await save_settings()
            await event.respond(f"📦 پک {name} برای {len(keys)} کانال")

        elif cmd == "packs":
            out = []
            for n, v in S["packs"].items():
                out.append(f"📦 {n}")
                out += [f"  {i}. {x}" for i, x in enumerate(v, 1)]
            await event.respond("\n".join(out) or "پکی نیست")

        elif cmd == "pack":
            lines = [x.strip() for x in rest.split("\n") if x.strip()]
            name, texts = lines[0], lines[1:]
            S["packs"].setdefault(name, []).extend(texts)
            await save_settings()
            await event.respond(f"✅ پک {name}: {len(S['packs'][name])} متن")

        elif cmd == "fmt":
            which, state = rest.lower().split()
            if which not in ("bold", "spoiler"):
                await event.respond("❌ فقط bold یا spoiler")
                return
            S.setdefault("fmt", {})[which] = state in ("on", "1", "yes")
            await save_settings()
            await event.respond(f"✅ {which}: {state}")

        elif cmd == "addtext":
            name, _, body = rest.partition("\n")
            name, body = name.strip(), body.strip()
            if not name or not body:
                await event.respond("❌ شکل درست:\n/addtext نام_پک\nمتن")
                return
            S["packs"].setdefault(name, []).append(body)
            await save_settings()
            await event.respond(f"✅ متن به پک {name} اضافه شد ({len(S['packs'][name])} متن)")

        elif cmd == "deltext":
            name, num = rest.rsplit(None, 1)
            S["packs"][name].pop(int(num) - 1)
            await save_settings()
            await event.respond("🗑 حذف شد")

        elif cmd == "delpack":
            S["packs"].pop(rest, None)
            await save_settings()
            await event.respond("🗑 پک حذف شد")

        elif cmd == "pause":
            S["enabled"] = False
            await save_settings()
            await event.respond("⏸ متوقف شد")

        elif cmd == "resume":
            S["enabled"] = True
            await save_settings()
            await event.respond("▶️ ادامه پیدا کرد")

    except Exception as e:
        await event.respond(f"❌ خطا: {e}\n(دستور /help را ببین)")


# ---------- صفحه ورود (فقط برای گرفتن SESSION) ----------
def page(body):
    return web.Response(
        content_type="text/html",
        text="<html><head><meta name=viewport content='width=device-width,initial-scale=1'></head>"
             "<body dir=rtl style='font-family:sans-serif;padding:20px;max-width:480px;margin:auto'>"
             f"{body}</body></html>",
    )


def authed(request):
    return request.query.get("key") == ADMIN_KEY


def form(action, fields, button):
    k = quote(ADMIN_KEY)
    inputs = "".join(
        f"<p><input name='{n}' placeholder='{html.escape(p)}' style='width:100%;padding:10px;font-size:16px'></p>"
        for n, p in fields
    )
    return f"<form method=post action='/login/{action}?key={k}'>{inputs}<button style='padding:10px 20px;font-size:16px'>{button}</button></form>"


async def health(request):
    return web.Response(text="ok")


async def login_page(request):
    if not authed(request):
        return web.Response(status=403, text="forbidden")
    if SESSION:
        return page("SESSION قبلاً تنظیم شده و ربات فعال است.")
    return page("<h3>ورود</h3>" + form("phone", [("phone", "شماره با کد کشور مثل +98912...")], "ارسال کد"))


async def login_phone(request):
    if not authed(request):
        return web.Response(status=403, text="forbidden")
    data = await request.post()
    try:
        c = TelegramClient(StringSession(), API_ID, API_HASH)
        await c.connect()
        phone = data["phone"].strip()
        sent = await c.send_code_request(phone)
        login_state.clear()
        login_state.update(client=c, phone=phone, hash=sent.phone_code_hash)
        return page("<h3>کد را وارد کن</h3>" + form("code", [("code", "کد تلگرام")], "ورود"))
    except Exception as e:
        return page(f"خطا: {html.escape(str(e))}")


async def login_code(request):
    if not authed(request):
        return web.Response(status=403, text="forbidden")
    data = await request.post()
    c = login_state.get("client")
    if not c:
        return page("اول از مرحله شماره شروع کن.")
    try:
        if login_state.get("need_pw"):
            await c.sign_in(password=data["password"])
        else:
            try:
                await c.sign_in(login_state["phone"], data["code"].strip().replace(" ", ""),
                                phone_code_hash=login_state["hash"])
            except errors.SessionPasswordNeededError:
                login_state["need_pw"] = True
                return page("<h3>رمز دومرحله‌ای</h3>" + form("code", [("password", "رمز تأیید دومرحله‌ای")], "ورود"))
        s = c.session.save()
        return page("<h3>✅ موفق</h3><p>این متن را کامل کپی کن و در Render با اسم SESSION بگذار:</p>"
                    f"<textarea style='width:100%;height:160px'>{s}</textarea>")
    except Exception as e:
        return page(f"خطا: {html.escape(str(e))}")


# ---------- اجرا ----------
async def main():
    global client
    app = web.Application()
    app.add_routes([
        web.get("/", health),
        web.get("/login", login_page),
        web.post("/login/phone", login_phone),
        web.post("/login/code", login_code),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("web server up on %s", PORT)

    if not SESSION:
        log.info("no SESSION -> login mode")
        await asyncio.Event().wait()

    await asyncio.sleep(STARTUP_DELAY)  # جلوگیری از اتصال همزمان دو نسخه با یک سشن
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        log.error("SESSION invalid")
        await asyncio.Event().wait()

    await client.get_dialogs()
    await load_settings()
    client.add_event_handler(on_post, events.NewMessage(incoming=True))
    client.add_event_handler(on_cmd, events.NewMessage(chats="me"))
    await client.send_message("me", "✅ ربات روشن شد. برای راهنما بنویس: /help")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
