import asyncio
import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
from deep_translator import GoogleTranslator
from telegram import Update
from telegram.constants import ParseMode
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

# Public destination
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", "").strip()  # example: @globaltechpulse

# Private admin review group / channel
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "").strip())  # numeric chat id
ADMIN_USER_IDS_RAW = os.getenv("ADMIN_USER_IDS", "").strip()  # example: 12345,67890
ADMIN_USER_IDS = {
    int(x.strip()) for x in ADMIN_USER_IDS_RAW.split(",") if x.strip().isdigit()
}

TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka").strip()
POST_HOURS_RAW = os.getenv("POST_HOURS", "9,20").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))  # 5 min
MAX_PENDING_PER_RUN = int(os.getenv("MAX_PENDING_PER_RUN", "3"))

# Optional Facebook page posting
FB_ENABLE_PUBLISH = os.getenv("FB_ENABLE_PUBLISH", "false").strip().lower() == "true"
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "").strip()

# Files
DATA_DIR = Path(".")
SEEN_FILE = DATA_DIR / "seen_items.json"
QUEUE_FILE = DATA_DIR / "review_queue.json"
STATE_FILE = DATA_DIR / "schedule_state.json"

# =========================
# SOURCES
# =========================
RSS_FEEDS = [
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
# FILE UTILS
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
# HELPERS
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


def shorten_text(text, limit=320):
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
    translated = shorten_text(to_bangla(base), 480)
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
        f"ID: {item['id']}\n"
        f"Title: {title}\n\n"
        f"{body}\n\n"
        f"Source: {source_name}\n"
        f"{link}\n\n"
        f"Commands:\n"
        f"/approve {item['id']}\n"
        f"/skip {item['id']}\n"
        f"/editcaption {item['id']} | তোমার নতুন caption\n\n"
        f"Edited photo/video attach করতে media send করে caption-এ দাও:\n"
        f"/attach {item['id']}"
    )


def build_public_caption(item):
    title = strip_html(item["title"])
    summary = strip_html(item["summary"])
    source_name = strip_html(item["source_name"])
    link = item["link"]

    if item.get("custom_caption"):
        return item["custom_caption"]

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
    short_summary = shorten_text(summary, 150)
    return (
        "🎥 REELS SCRIPT\n\n"
        f"Hook:\nআজকের সবচেয়ে বড় খবর — {title}\n\n"
        f"Body:\n{short_summary}\n\n"
        f"CTA:\nআরও এমন আপডেট পেতে join করুন: {PUBLIC_CHANNEL_ID}"
    )


# =========================
# MEDIA EXTRACT
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
# RSS FETCH
# =========================
def fetch_rss_candidates():
    seen = load_json(SEEN_FILE, [])
    seen_links = {x.get("link", "") for x in seen}
    seen_titles = [x.get("title", "") for x in seen]

    out = []

    for source_name, feed_url in RSS_FEEDS:
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
            if not is_valid_news(title, summary, source_name):
                continue

            out.append({
                "title": title,
                "summary": summary if summary else "Latest update from the source.",
                "link": link,
                "source_name": source_name,
                "image_url": extract_image(entry),
                "score": score_news(title, summary, source_name),
            })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:MAX_PENDING_PER_RUN]


# =========================
# SCHEDULE
# =========================
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


# =========================
# ADMIN PERMISSION
# =========================
def is_admin_user(user_id: int) -> bool:
    if not ADMIN_USER_IDS:
        return True
    return user_id in ADMIN_USER_IDS


# =========================
# QUEUE
# =========================
def load_queue():
    return load_json(QUEUE_FILE, [])


def save_queue(queue):
    save_json(QUEUE_FILE, queue)


def next_pending_id(queue):
    if not queue:
        return 1
    return max(item["id"] for item in queue) + 1


def add_seen_item(item):
    seen = load_json(SEEN_FILE, [])
    seen.append({
        "title": item["title"],
        "link": item["link"],
        "source_name": item["source_name"],
        "saved_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat()
    })
    save_json(SEEN_FILE, seen)


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
# PUBLISH HELPERS
# =========================
async def publish_item(app: Application, item: dict):
    bot = app.bot
    caption = build_public_caption(item)

    # priority 1: attached telegram media
    if item.get("attached_type") and item.get("attached_file_id"):
        if item["attached_type"] == "photo":
            await bot.send_photo(chat_id=PUBLIC_CHANNEL_ID, photo=item["attached_file_id"], caption=caption[:1024])
        elif item["attached_type"] == "video":
            await bot.send_video(chat_id=PUBLIC_CHANNEL_ID, video=item["attached_file_id"], caption=caption[:1024])

        # Facebook upload if possible: need local file download from Telegram
        if FB_ENABLE_PUBLISH and item["attached_type"] in {"photo", "video"}:
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

    # priority 2: feed image url
    if item.get("image_url"):
        await bot.send_photo(chat_id=PUBLIC_CHANNEL_ID, photo=item["image_url"], caption=caption[:1024])

    else:
        await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=caption)

    # FB fallback text+link
    fb_post_text(caption, item["link"])


# =========================
# BOT COMMANDS
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return
    await update.message.reply_text(
        "Bot ready.\n\n"
        "Commands:\n"
        "/status\n"
        "/approve ID\n"
        "/skip ID\n"
        "/editcaption ID | নতুন caption\n\n"
        "Edited media attach:\n"
        "photo/video send করে caption-এ লিখো:\n"
        "/attach ID"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    queue = load_queue()
    pending = [x for x in queue if x["status"] == "pending"]
    approved = [x for x in queue if x["status"] == "approved"]
    skipped = [x for x in queue if x["status"] == "skipped"]

    await update.message.reply_text(
        f"Queue status:\n"
        f"Pending: {len(pending)}\n"
        f"Approved: {len(approved)}\n"
        f"Skipped: {len(skipped)}"
    )


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Use: /approve 12")
        return

    item_id = int(context.args[0])
    queue = load_queue()

    item = next((x for x in queue if x["id"] == item_id and x["status"] == "pending"), None)
    if not item:
        await update.message.reply_text("Pending item not found.")
        return

    try:
        await publish_item(context.application, item)
        item["status"] = "approved"
        item["approved_at"] = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
        save_queue(queue)
        add_seen_item(item)

        await update.message.reply_text(f"Approved and posted: {item_id}")

        reel = generate_reel_script(item)
        await update.message.reply_text(reel)
    except Exception as e:
        await update.message.reply_text(f"Approve failed: {e}")


async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Use: /skip 12")
        return

    item_id = int(context.args[0])
    queue = load_queue()

    item = next((x for x in queue if x["id"] == item_id and x["status"] == "pending"), None)
    if not item:
        await update.message.reply_text("Pending item not found.")
        return

    item["status"] = "skipped"
    item["skipped_at"] = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
    save_queue(queue)
    add_seen_item(item)

    await update.message.reply_text(f"Skipped: {item_id}")


async def editcaption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    raw = update.message.text or ""
    # format: /editcaption 12 | your new caption
    m = re.match(r"^/editcaption\s+(\d+)\s*\|\s*(.+)$", raw, flags=re.DOTALL)
    if not m:
        await update.message.reply_text("Use: /editcaption 12 | তোমার নতুন caption")
        return

    item_id = int(m.group(1))
    new_caption = m.group(2).strip()

    queue = load_queue()
    item = next((x for x in queue if x["id"] == item_id and x["status"] == "pending"), None)
    if not item:
        await update.message.reply_text("Pending item not found.")
        return

    item["custom_caption"] = new_caption
    save_queue(queue)
    await update.message.reply_text(f"Caption updated for ID {item_id}")


async def media_attach_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return
    if not update.message:
        return

    caption = update.message.caption or ""
    m = re.match(r"^/attach\s+(\d+)$", caption.strip())
    if not m:
        return

    item_id = int(m.group(1))
    queue = load_queue()
    item = next((x for x in queue if x["id"] == item_id and x["status"] == "pending"), None)
    if not item:
        await update.message.reply_text("Pending item not found for attach.")
        return

    if update.message.video:
        item["attached_type"] = "video"
        item["attached_file_id"] = update.message.video.file_id
    elif update.message.photo:
        item["attached_type"] = "photo"
        item["attached_file_id"] = update.message.photo[-1].file_id
    else:
        await update.message.reply_text("Send photo/video with caption: /attach ID")
        return

    save_queue(queue)
    await update.message.reply_text(f"Media attached for ID {item_id}")


# =========================
# COLLECTOR TASK
# =========================
async def collector_loop(app: Application):
    await asyncio.sleep(5)

    while True:
        try:
            can_collect, now_dt, slot_key = should_collect_now()
            print(f"[CHECK] {now_dt}")

            if can_collect and slot_key:
                print(f"[COLLECT] {slot_key}")
                candidates = fetch_rss_candidates()

                if candidates:
                    queue = load_queue()
                    current_pending_titles = [x["title"] for x in queue if x["status"] == "pending"]

                    added = 0
                    for cand in candidates:
                        if is_similar_title(cand["title"], current_pending_titles):
                            continue

                        cand["id"] = next_pending_id(queue)
                        cand["status"] = "pending"
                        cand["created_at"] = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
                        cand["attached_type"] = None
                        cand["attached_file_id"] = None
                        cand["custom_caption"] = None

                        queue.append(cand)
                        save_queue(queue)

                        text = build_pending_caption(cand)
                        if cand.get("image_url"):
                            await app.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=cand["image_url"], caption=text[:1024])
                        else:
                            await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

                        added += 1

                    print(f"[COLLECTED] {added} new pending items")
                else:
                    print("[COLLECTED] no new items")

                mark_collected(slot_key)
            else:
                print("[WAIT] not collection window")

        except Exception as e:
            print(f"[COLLECTOR ERROR] {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# =========================
# MAIN
# =========================
async def post_init(app: Application):
    app.create_task(collector_loop(app))


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")
    if not PUBLIC_CHANNEL_ID:
        raise ValueError("PUBLIC_CHANNEL_ID is missing")
    if not ADMIN_CHAT_ID:
        raise ValueError("ADMIN_CHAT_ID is missing")

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
    app.add_handler(
        MessageHandler((filters.PHOTO | filters.VIDEO) & filters.Caption(True), media_attach_handler)
    )

    app.run_polling()


if __name__ == "__main__":
    main()
