import asyncio
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
from deep_translator import GoogleTranslator
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "").strip())
ADMIN_USER_IDS_RAW = os.getenv("ADMIN_USER_IDS", "").strip()
ADMIN_USER_IDS = {
    int(x.strip()) for x in ADMIN_USER_IDS_RAW.split(",") if x.strip().isdigit()
}

TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka").strip()
POST_HOURS_RAW = os.getenv("POST_HOURS", "9,20").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))
MAX_PENDING_PER_RUN = int(os.getenv("MAX_PENDING_PER_RUN", "5"))

FB_ENABLE_PUBLISH = os.getenv("FB_ENABLE_PUBLISH", "false").strip().lower() == "true"
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "").strip()

DATA_DIR = Path(".")
SEEN_FILE = DATA_DIR / "seen_items.json"
QUEUE_FILE = DATA_DIR / "review_queue.json"
STATE_FILE = DATA_DIR / "schedule_state.json"
SOURCES_FILE = DATA_DIR / "custom_sources.json"

DEFAULT_RSS_FEEDS = [
    ("Prothom Alo", "https://www.prothomalo.com/feed"),
    ("BDNews24", "https://bdnews24.com/feed/"),
    ("Bangla Tribune", "https://banglatribune.com/feed/"),
    ("The Daily Star", "https://www.thedailystar.net/frontpage/rss.xml"),
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
]

BANGLADESH_KEYWORDS = [
    "bangladesh", "বাংলাদেশ", "dhaka", "ঢাকা", "chattogram", "চট্টগ্রাম",
    "sylhet", "সিলেট", "rajshahi", "রাজশাহী", "khulna", "খুলনা",
    "barishal", "বরিশাল", "rangpur", "রংপুর", "mymensingh", "ময়মনসিংহ"
]

WORLD_IMPORTANT_KEYWORDS = [
    "war", "iran", "usa", "america", "israel", "china", "russia", "ukraine",
    "missile", "attack", "military", "conflict", "government", "president",
    "sanction", "border", "security", "breaking", "urgent", "earthquake", "crisis"
]

TECH_KEYWORDS = [
    "ai", "artificial intelligence", "chatgpt", "openai", "robot", "robotics",
    "gadget", "gadgets", "smartphone", "iphone", "android", "mobile",
    "chip", "processor", "launch", "device", "wearable", "laptop", "camera",
    "tesla", "google", "meta", "samsung", "apple", "startup", "innovation",
    "cyber", "security", "software", "hardware", "drone", "vr", "ar",
    "tool", "app", "future tech", "mobile leak", "leak"
]

BORING_KEYWORDS = [
    "coupon", "discount", "conference pass", "ticket", "sale ends",
    "subscribe now", "investor presentation", "earnings call",
    "quarterly report", "shareholder", "promo"
]

# =========================
# FILE HELPERS
# =========================
def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# BASIC HELPERS
# =========================
def parse_post_hours(raw: str):
    hours = []
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            h = int(x)
            if 0 <= h <= 23:
                hours.append(h)
    return hours if hours else [9, 20]


POST_HOURS = parse_post_hours(POST_HOURS_RAW)


def strip_html(raw_text):
    text = re.sub(r"<.*?>", "", raw_text or "")
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def shorten_text(text, limit=420):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + "..."


def normalize_text(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\u0980-\u09FF ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def contains_any(text, keywords):
    text = (text or "").lower()
    return any(word in text for word in keywords)


def is_admin_user(user_id: int) -> bool:
    if not ADMIN_USER_IDS:
        return True
    return user_id in ADMIN_USER_IDS


# =========================
# CONTENT CLASSIFICATION
# =========================
def classify_news(title, summary, source_name):
    text = f"{title} {summary} {source_name}".lower()

    if contains_any(text, BORING_KEYWORDS):
        return "boring"

    if contains_any(text, BANGLADESH_KEYWORDS) or source_name in [
        "Prothom Alo", "BDNews24", "Bangla Tribune", "The Daily Star"
    ]:
        return "bangladesh"

    if contains_any(text, WORLD_IMPORTANT_KEYWORDS):
        return "world"

    if contains_any(text, TECH_KEYWORDS):
        return "tech"

    return "other"


def is_valid_news(title, summary, source_name):
    return classify_news(title, summary, source_name) in {"bangladesh", "world", "tech"}


def is_breaking_news(title, summary):
    text = f"{title} {summary}".lower()
    return contains_any(text, [
        "breaking", "urgent", "war", "attack", "missile", "iran",
        "usa", "israel", "china", "russia", "crisis", "earthquake"
    ])


def score_news(title, summary, source_name):
    text = f"{title} {summary} {source_name}".lower()
    category = classify_news(title, summary, source_name)
    score = 0

    if category == "bangladesh":
        score += 6
    elif category == "world":
        score += 5
    elif category == "tech":
        score += 4

    if is_breaking_news(title, summary):
        score += 4

    if contains_any(text, ["ai", "chatgpt", "openai"]):
        score += 3

    if contains_any(text, ["iphone", "android", "mobile", "smartphone", "leak", "launch"]):
        score += 2

    if len(title) < 120:
        score += 1

    return score


def is_similar_title(new_title, seen_titles):
    new_norm = normalize_text(new_title)
    if not new_norm:
        return False
    for old in seen_titles:
        old_norm = normalize_text(old)
        if not old_norm:
            continue
        if new_norm == old_norm or new_norm in old_norm or old_norm in new_norm:
            return True
    return False


# =========================
# SUMMARY / CAPTIONS
# =========================
def to_bangla(text):
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return GoogleTranslator(source="auto", target="bn").translate(text).strip()
    except Exception:
        return text


def make_bangla_summary(title, summary, source_name):
    base = f"{title}. {shorten_text(summary, 260)}"
    translated = shorten_text(to_bangla(base), 520)
    category = classify_news(title, summary, source_name)

    if category == "bangladesh":
        prefix = "বাংলাদেশ আপডেট:"
    elif category == "world":
        prefix = "বিশ্বের গুরুত্বপূর্ণ খবর:"
    else:
        prefix = "টেক আপডেট:"

    return f"{prefix} {translated}"


def build_pending_caption(item):
    title = strip_html(item["title"])
    summary = strip_html(item["summary"])
    source_name = strip_html(item["source_name"])
    link = item["link"]

    if is_breaking_news(title, summary):
        header = "🚨 PENDING: ব্রেকিং নিউজ"
    else:
        category = classify_news(title, summary, source_name)
        if category == "bangladesh":
            header = "🇧🇩 PENDING: বাংলাদেশ"
        elif category == "world":
            header = "🌍 PENDING: বিশ্ব"
        else:
            header = "📱 PENDING: Tech"

    body = make_bangla_summary(title, summary, source_name)

    return (
        f"{header}\n\n"
        f"Title: {title}\n\n"
        f"{body}\n\n"
        f"Source: {source_name}\n"
        f"{link}\n\n"
        f"Reply commands:\n"
        f"/approve\n"
        f"/skip\n"
        f"/editcaption তোমার নতুন caption\n\n"
        f"Edited photo/video attach করতে এই pending post-এ reply করে media send করো।"
    )


def build_public_caption(item):
    if item.get("custom_caption"):
        return item["custom_caption"]

    title = strip_html(item["title"])
    summary = strip_html(item["summary"])
    source_name = strip_html(item["source_name"])
    link = item["link"]

    if is_breaking_news(title, summary):
        header = "🚨 ব্রেকিং নিউজ"
    else:
        category = classify_news(title, summary, source_name)
        if category == "bangladesh":
            header = "🇧🇩 বাংলাদেশের গুরুত্বপূর্ণ আপডেট"
        elif category == "world":
            header = "🌍 বিশ্বের জরুরি খবর"
        else:
            header = "📱 AI / Gadget / Tech Update"

    return (
        f"{header}\n\n"
        f"{title}\n\n"
        f"{make_bangla_summary(title, summary, source_name)}\n\n"
        f"Source: {source_name}\n"
        f"{link}"
    )


def generate_reel_script(item):
    title = strip_html(item["title"])
    summary = strip_html(item["summary"])
    short_summary = shorten_text(summary, 160)
    return (
        "🎥 REELS SCRIPT\n\n"
        f"Hook:\nআজকের সবচেয়ে বড় খবর — {title}\n\n"
        f"Body:\n{short_summary}\n\n"
        f"CTA:\nআরও এমন আপডেট পেতে join করুন: {PUBLIC_CHANNEL_ID}"
    )


# =========================
# SOURCES
# =========================
def load_sources():
    existing = load_json(SOURCES_FILE, None)
    if existing is None:
        save_json(SOURCES_FILE, DEFAULT_RSS_FEEDS.copy())
        return DEFAULT_RSS_FEEDS.copy()
    return existing


def save_sources(sources):
    save_json(SOURCES_FILE, sources)


# =========================
# MEDIA
# =========================
def extract_image(entry):
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        for item in media_content:
            media_url = item.get("url")
            media_type = item.get("type", "")
            if media_url and str(media_type).startswith("image/"):
                return media_url

    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail and isinstance(media_thumbnail, list):
        for item in media_thumbnail:
            thumb_url = item.get("url")
            if thumb_url:
                return thumb_url

    links = getattr(entry, "links", [])
    for item in links:
        href = item.get("href")
        media_type = item.get("type", "")
        if href and str(media_type).startswith("image/"):
            return href

    summary = getattr(entry, "summary", "") or ""
    match = re.search(r'<img[^>]+src="([^"]+)"', summary)
    if match:
        return match.group(1)

    return None


# =========================
# SEEN / QUEUE
# =========================
def load_seen():
    return load_json(SEEN_FILE, [])


def save_seen(data):
    save_json(SEEN_FILE, data)


def add_seen_item(item):
    seen = load_seen()
    seen.append({
        "title": item["title"],
        "link": item["link"],
        "saved_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat()
    })
    save_seen(seen)


def load_queue():
    return load_json(QUEUE_FILE, [])


def save_queue(queue):
    save_json(QUEUE_FILE, queue)


def find_pending_by_reply(queue, reply_message_id):
    return next(
        (x for x in queue if x.get("status") == "pending" and x.get("admin_message_id") == reply_message_id),
        None
    )


# =========================
# FETCH
# =========================
def fetch_rss_candidates():
    seen = load_seen()
    seen_links = {x.get("link", "") for x in seen}
    seen_titles = [x.get("title", "") for x in seen]

    queue = load_queue()
    pending_titles = [x.get("title", "") for x in queue if x.get("status") == "pending"]

    out = []
    sources = load_sources()

    for source in sources:
        if not isinstance(source, list) or len(source) != 2:
            continue

        source_name, feed_url = source

        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[ERROR] Feed parse failed for {source_name}: {e}")
            continue

        entries = getattr(feed, "entries", [])
        for entry in entries[:12]:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            summary = strip_html(raw_summary)

            if not title or not link:
                continue
            if link in seen_links:
                continue
            if is_similar_title(title, seen_titles):
                continue
            if is_similar_title(title, pending_titles):
                continue
            if not is_valid_news(title, summary, source_name):
                continue

            out.append({
                "title": title,
                "summary": summary if summary else "Latest update from the source.",
                "link": link,
                "source_name": source_name,
                "image_url": extract_image(entry),
                "score": score_news(title, summary, source_name),
                "status": "pending",
                "custom_caption": None,
                "attached_type": None,
                "attached_file_id": None,
            })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:MAX_PENDING_PER_RUN]


# =========================
# FACEBOOK PUBLISH
# =========================
def fb_post_text(message: str, link: str):
    if not (FB_ENABLE_PUBLISH and FB_PAGE_ID and FB_PAGE_TOKEN):
        return

    url = f"https://graph.facebook.com/{FB_PAGE_ID}/feed"
    payload = {
        "message": message,
        "link": link,
        "access_token": FB_PAGE_TOKEN
    }
    requests.post(url, data=payload, timeout=60)


def fb_post_photo(file_path: str, caption: str):
    if not (FB_ENABLE_PUBLISH and FB_PAGE_ID and FB_PAGE_TOKEN):
        return

    url = f"https://graph.facebook.com/{FB_PAGE_ID}/photos"
    with open(file_path, "rb") as f:
        requests.post(
            url,
            data={"caption": caption, "access_token": FB_PAGE_TOKEN},
            files={"source": f},
            timeout=120
        )


def fb_post_video(file_path: str, caption: str):
    if not (FB_ENABLE_PUBLISH and FB_PAGE_ID and FB_PAGE_TOKEN):
        return

    url = f"https://graph.facebook.com/{FB_PAGE_ID}/videos"
    with open(file_path, "rb") as f:
        requests.post(
            url,
            data={"description": caption, "access_token": FB_PAGE_TOKEN},
            files={"source": f},
            timeout=300
        )


# =========================
# PUBLISH
# =========================
async def publish_item(app: Application, item: dict):
    bot = app.bot
    caption = build_public_caption(item)

    if item.get("attached_type") and item.get("attached_file_id"):
        if item["attached_type"] == "photo":
            await bot.send_photo(chat_id=PUBLIC_CHANNEL_ID, photo=item["attached_file_id"], caption=caption[:1024])
        elif item["attached_type"] == "video":
            await bot.send_video(chat_id=PUBLIC_CHANNEL_ID, video=item["attached_file_id"], caption=caption[:1024])

        if FB_ENABLE_PUBLISH:
            tg_file = await bot.get_file(item["attached_file_id"])
            suffix = ".jpg" if item["attached_type"] == "photo" else ".mp4"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name
            await tg_file.download_to_drive(custom_path=tmp_path)

            try:
                if item["attached_type"] == "photo":
                    fb_post_photo(tmp_path, caption)
                else:
                    fb_post_video(tmp_path, caption)
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        return

    if item.get("image_url"):
        await bot.send_photo(chat_id=PUBLIC_CHANNEL_ID, photo=item["image_url"], caption=caption[:1024])
    else:
        await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=caption)

    fb_post_text(caption, item["link"])


# =========================
# COMMANDS
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return
    await update.message.reply_text(
        "Bot ready.\n\n"
        "Reply-based commands:\n"
        "Reply to pending post with:\n"
        "/approve\n"
        "/skip\n"
        "/editcaption তোমার নতুন caption\n\n"
        "Edited photo/video attach:\n"
        "pending post-এ reply করে media send করো\n\n"
        "Source commands:\n"
        "/addsource Name | RSS_URL\n"
        "/listsources\n"
        "/fetchnow"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    queue = load_queue()
    pending = [x for x in queue if x["status"] == "pending"]
    approved = [x for x in queue if x["status"] == "approved"]
    skipped = [x for x in queue if x["status"] == "skipped"]

    await update.message.reply_text(
        f"Queue status:\nPending: {len(pending)}\nApproved: {len(approved)}\nSkipped: {len(skipped)}"
    )


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Pending post-এ reply করে /approve দাও।")
        return

    queue = load_queue()
    item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
    if not item:
        await update.message.reply_text("এই reply message-এর সাথে pending item মেলেনি।")
        return

    try:
        await publish_item(context.application, item)
        item["status"] = "approved"
        item["approved_at"] = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
        save_queue(queue)
        add_seen_item(item)

        await update.message.reply_text("Approved and posted.")
        await update.message.reply_text(generate_reel_script(item))
    except Exception as e:
        await update.message.reply_text(f"Approve failed: {e}")


async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Pending post-এ reply করে /skip দাও।")
        return

    queue = load_queue()
    item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
    if not item:
        await update.message.reply_text("এই reply message-এর সাথে pending item মেলেনি।")
        return

    item["status"] = "skipped"
    item["skipped_at"] = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
    save_queue(queue)
    add_seen_item(item)

    await update.message.reply_text("Skipped.")


async def editcaption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Pending post-এ reply করে /editcaption নতুন caption দাও।")
        return

    raw = update.message.text or ""
    new_caption = raw.replace("/editcaption", "", 1).strip()
    if not new_caption:
        await update.message.reply_text("Use: /editcaption তোমার নতুন caption")
        return

    queue = load_queue()
    item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
    if not item:
        await update.message.reply_text("এই reply message-এর সাথে pending item মেলেনি।")
        return

    item["custom_caption"] = new_caption
    save_queue(queue)
    await update.message.reply_text("Caption updated.")


async def addsource_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    raw = update.message.text or ""
    payload = raw.replace("/addsource", "", 1).strip()
    if "|" not in payload:
        await update.message.reply_text("Use: /addsource Name | RSS_URL")
        return

    name, url = [x.strip() for x in payload.split("|", 1)]
    if not name or not url:
        await update.message.reply_text("Use: /addsource Name | RSS_URL")
        return

    sources = load_sources()
    sources.append([name, url])
    save_sources(sources)
    await update.message.reply_text(f"Source added:\n{name}\n{url}")


async def listsources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    sources = load_sources()
    if not sources:
        await update.message.reply_text("No sources found.")
        return

    text = "Current sources:\n\n" + "\n".join([f"- {name} | {url}" for name, url in sources[:100]])
    await update.message.reply_text(text)


async def fetchnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    await update.message.reply_text("Fetching now...")
    added = await collect_now(context.application)
    await update.message.reply_text(f"Fetch done. Added: {added}")


async def media_attach_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return
    if not update.message or not update.message.reply_to_message:
        return

    queue = load_queue()
    item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
    if not item:
        return

    if update.message.video:
        item["attached_type"] = "video"
        item["attached_file_id"] = update.message.video.file_id
        save_queue(queue)
        await update.message.reply_text("Edited video attached.")
        return

    if update.message.photo:
        item["attached_type"] = "photo"
        item["attached_file_id"] = update.message.photo[-1].file_id
        save_queue(queue)
        await update.message.reply_text("Edited photo attached.")
        return


# =========================
# COLLECT
# =========================
async def collect_now(app: Application):
    candidates = fetch_rss_candidates()
    queue = load_queue()
    added = 0

    for cand in candidates:
        text = build_pending_caption(cand)

        if cand.get("image_url"):
            sent = await app.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=cand["image_url"], caption=text[:1024])
        else:
            sent = await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

        cand["admin_message_id"] = sent.message_id
        queue.append(cand)
        added += 1

    save_queue(queue)
    return added


def get_slot_key(now_dt):
    return f"{now_dt.strftime('%Y-%m-%d')}_{now_dt.hour}"


def should_collect_now():
    now_dt = datetime.now(ZoneInfo(TIMEZONE))
    if now_dt.hour not in POST_HOURS:
        return False, now_dt, None

    state = load_json(STATE_FILE, {})
    slot_key = get_slot_key(now_dt)

    if state.get("last_collect_slot") == slot_key:
        return False, now_dt, slot_key

    return True, now_dt, slot_key


def mark_collected(slot_key):
    save_json(STATE_FILE, {"last_collect_slot": slot_key})


async def collector_loop(app: Application):
    await asyncio.sleep(5)

    while True:
        try:
            can_collect, now_dt, slot_key = should_collect_now()
            print(f"[CHECK] {now_dt}")

            if can_collect and slot_key:
                added = await collect_now(app)
                mark_collected(slot_key)
                print(f"[COLLECTED] {added}")
            else:
                print("[WAIT] not collection window")

        except Exception as e:
            print(f"[COLLECTOR ERROR] {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(app: Application):
    app.create_task(collector_loop(app))


# =========================
# MAIN
# =========================
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")
    if not PUBLIC_CHANNEL_ID:
        raise ValueError("PUBLIC_CHANNEL_ID is missing")

    print("===================================")
    print("Bot starting...")
    print(f"Public Channel: {PUBLIC_CHANNEL_ID}")
    print(f"Admin Chat ID: {ADMIN_CHAT_ID}")
    print(f"Post hours: {POST_HOURS}")
    print(f"Check interval: {CHECK_INTERVAL}")
    print(f"Facebook publish enabled: {FB_ENABLE_PUBLISH}")
    print("===================================")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("editcaption", editcaption_cmd))
    app.add_handler(CommandHandler("addsource", addsource_cmd))
    app.add_handler(CommandHandler("listsources", listsources_cmd))
    app.add_handler(CommandHandler("fetchnow", fetchnow_cmd))
    app.add_handler(MessageHandler((filters.PHOTO | filters.VIDEO), media_attach_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
