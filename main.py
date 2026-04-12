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

# =========================================================
# ENV
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID", "").strip()

ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID", "").strip()
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW) if ADMIN_CHAT_ID_RAW.lstrip("-").isdigit() else 0

ADMIN_USER_IDS_RAW = os.getenv("ADMIN_USER_IDS", "").strip()
ADMIN_USER_IDS = {
    int(x.strip()) for x in ADMIN_USER_IDS_RAW.split(",")
    if x.strip().lstrip("-").isdigit()
}

TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka").strip()
POST_HOURS_RAW = os.getenv(
    "POST_HOURS",
    "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23"
).strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "600"))
MAX_PENDING_PER_RUN = int(os.getenv("MAX_PENDING_PER_RUN", "5"))

FB_ENABLE_PUBLISH = os.getenv("FB_ENABLE_PUBLISH", "false").strip().lower() == "true"
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "").strip()

DATA_DIR = Path(".")
SEEN_FILE = DATA_DIR / "seen_items.json"
QUEUE_FILE = DATA_DIR / "review_queue.json"
STATE_FILE = DATA_DIR / "schedule_state.json"
SOURCES_FILE = DATA_DIR / "custom_sources.json"

# =========================================================
# DEFAULT SOURCES
# =========================================================
DEFAULT_SOURCES = [
    {"name": "Prothom Alo", "url": "https://www.prothomalo.com/feed", "type": "feed", "mode": "auto"},
    {"name": "Bangla Tribune", "url": "https://banglatribune.com/feed/", "type": "feed", "mode": "auto"},
    {"name": "The Daily Star", "url": "https://www.thedailystar.net/frontpage/rss.xml", "type": "feed", "mode": "auto"},
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "type": "feed", "mode": "auto"},
    {"name": "BBC Technology", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml", "type": "feed", "mode": "auto"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "type": "feed", "mode": "auto"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "type": "feed", "mode": "auto"},
    {"name": "BDNews24 Main", "url": "https://bdnews24.com/feed/", "type": "feed", "mode": "auto"},
    {"name": "bdnews24 Politics", "url": "https://bangla.bdnews24.com/politics/?getXmlFeed=true&widgetId=1151&widgetName=rssfeed", "type": "feed", "mode": "auto"},
    {"name": "bdnews24 World", "url": "https://bangla.bdnews24.com/world/?getXmlFeed=true&widgetId=1215510&widgetName=rssfeed", "type": "feed", "mode": "auto"},
    {"name": "bdnews24 Business", "url": "https://bdnews24.com/business/?getXmlFeed=true&widgetId=1210&widgetName=rssfeed", "type": "feed", "mode": "auto"},

    # YouTube news feeds
    {"name": "BBC News YouTube", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC16niRr50-MSBwiO3YDb3RA", "type": "feed", "mode": "auto"},
    {"name": "Al Jazeera English YouTube", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCNye-wNBqNL5ZzHSJj3l8Bg", "type": "feed", "mode": "auto"},
    {"name": "Reuters YouTube", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UChqUTb7kYRX8-EiaN3XFrSQ", "type": "feed", "mode": "auto"},
    {"name": "DW News YouTube", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCknLrEdhRCp1aegoMqRaCZg", "type": "feed", "mode": "auto"},
    {"name": "Jamuna TV YouTube", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCN6sm8iHiPd0cnoUardDAnA", "type": "feed", "mode": "auto"},
    {"name": "Somoy TV YouTube", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCxHoBXkY88Tb8z1Ssj6CWsQ", "type": "feed", "mode": "auto"},

    # Example Facebook review source
    # {"name": "Facebook Page 1", "url": "https://REAL-FACEBOOK-FEED-OR-BRIDGE-URL", "type": "feed", "mode": "review"},
]

BANGLADESH_KEYWORDS = [
    "bangladesh", "বাংলাদেশ", "dhaka", "ঢাকা", "chattogram", "চট্টগ্রাম",
    "sylhet", "সিলেট", "rajshahi", "রাজশাহী", "khulna", "খুলনা",
    "barishal", "বরিশাল", "rangpur", "রংপুর", "mymensingh", "ময়মনসিংহ",
    "cumilla", "কুমিল্লা", "noakhali", "নোয়াখালী", "feni", "ফেনী",
    "bogura", "বগুড়া", "gazipur", "গাজীপুর", "narayanganj", "নারায়ণগঞ্জ",
    "jessore", "যশোর", "rajbari", "রাজবাড়ী", "kushtia", "কুষ্টিয়া",
    "pabna", "পাবনা", "dinajpur", "দিনাজপুর", "sunamganj", "সুনামগঞ্জ"
]

WORLD_IMPORTANT_KEYWORDS = [
    "war", "iran", "usa", "america", "israel", "china", "russia", "ukraine",
    "missile", "attack", "military", "conflict", "government", "president",
    "sanction", "border", "security", "breaking", "urgent", "earthquake", "crisis",
    "gaza", "palestine", "syria", "iranian", "israeli"
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
    "quarterly report", "shareholder", "promo", "podcast", "livestream"
]

# =========================================================
# JSON HELPERS
# =========================================================
def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================================================
# BASIC HELPERS
# =========================================================
def parse_post_hours(raw: str):
    hours = []
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            h = int(x)
            if 0 <= h <= 23:
                hours.append(h)
    return hours if hours else list(range(24))


POST_HOURS = parse_post_hours(POST_HOURS_RAW)


def strip_html(raw_text):
    text = re.sub(r"<.*?>", "", raw_text or "")
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def shorten_text(text, limit=550):
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


def now_iso():
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat()


def source_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "source"


# =========================================================
# SOURCES
# =========================================================
def normalize_source(source: dict):
    source = dict(source or {})
    source["name"] = str(source.get("name", "")).strip()
    source["url"] = str(source.get("url", "")).strip()
    source["type"] = str(source.get("type", "feed")).strip().lower() or "feed"
    source["mode"] = str(source.get("mode", "auto")).strip().lower() or "auto"
    source["enabled"] = bool(source.get("enabled", True))
    source["fail_count"] = int(source.get("fail_count", 0))
    source["last_error"] = str(source.get("last_error", "")).strip()
    source["last_ok_at"] = str(source.get("last_ok_at", "")).strip()
    source["slug"] = source_slug(source["name"])
    return source


def load_sources():
    existing = load_json(SOURCES_FILE, None)
    if existing is None:
        sources = [normalize_source(x) for x in DEFAULT_SOURCES]
        save_json(SOURCES_FILE, sources)
        return sources

    sources = []
    for item in existing:
        if isinstance(item, dict):
            sources.append(normalize_source(item))
    save_json(SOURCES_FILE, sources)
    return sources


def save_sources(sources):
    clean = [normalize_source(s) for s in sources if isinstance(s, dict)]
    save_json(SOURCES_FILE, clean)


def reset_sources_to_default():
    sources = [normalize_source(x) for x in DEFAULT_SOURCES]
    save_sources(sources)
    return sources


def mark_source_result(source_name: str, ok: bool, error: str = ""):
    sources = load_sources()
    updated = False

    for src in sources:
        if src.get("name") == source_name:
            if ok:
                src["fail_count"] = 0
                src["last_error"] = ""
                src["last_ok_at"] = now_iso()
            else:
                src["fail_count"] = int(src.get("fail_count", 0)) + 1
                src["last_error"] = error[:500]

                if "your-facebook-feed-or-bridge-url" in src.get("url", ""):
                    src["enabled"] = False

                if src["fail_count"] >= 10:
                    src["enabled"] = False

            updated = True
            break

    if updated:
        save_sources(sources)


# =========================================================
# SEEN / QUEUE
# =========================================================
def load_seen():
    return load_json(SEEN_FILE, [])


def save_seen(data):
    save_json(SEEN_FILE, data)


def add_seen_item(item):
    seen = load_seen()
    seen.append({
        "title": item["title"],
        "link": item["link"],
        "saved_at": now_iso()
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


def get_pending_items():
    return [x for x in load_queue() if x.get("status") == "pending"]


def get_approved_items():
    return [x for x in load_queue() if x.get("status") == "approved"]


def next_pending_indexed():
    pending = get_pending_items()
    return [(idx + 1, item) for idx, item in enumerate(pending)]


def find_pending_by_index(index_number: int):
    for idx, item in next_pending_indexed():
        if idx == index_number:
            return item
    return None


# =========================================================
# CLASSIFY
# =========================================================
def classify_news(title, summary, source_name):
    text = f"{title} {summary} {source_name}".lower()

    if contains_any(text, BORING_KEYWORDS):
        return "boring"

    if contains_any(text, BANGLADESH_KEYWORDS) or source_name in {
        "Prothom Alo", "BDNews24 Main", "Bangla Tribune", "The Daily Star",
        "bdnews24 Politics", "bdnews24 World", "bdnews24 Business",
        "Jamuna TV YouTube", "Somoy TV YouTube"
    }:
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
        "usa", "israel", "china", "russia", "crisis", "earthquake",
        "blast", "dead", "killed", "injured"
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

    if contains_any(text, ["google", "meta", "apple", "tesla", "microsoft"]):
        score += 1

    if len(title) < 150:
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


# =========================================================
# SUMMARY
# =========================================================
def to_bangla(text):
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return GoogleTranslator(source="auto", target="bn").translate(text).strip()
    except Exception:
        return text


def make_english_summary(title, summary):
    title = strip_html(title)
    summary = strip_html(summary)

    if not summary:
        return shorten_text(title, 350)

    text = f"{title}. {summary}"
    text = strip_html(text)

    parts = re.split(r"(?<=[.!?])\s+", text)
    useful_parts = []

    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) < 25:
            continue
        useful_parts.append(p)

        if len(" ".join(useful_parts)) >= 500:
            break

    if not useful_parts:
        return shorten_text(text, 500)

    return shorten_text(" ".join(useful_parts), 500)


def make_bangla_summary(title, summary, source_name):
    english = make_english_summary(title, summary)
    translated = shorten_text(to_bangla(english), 900)
    category = classify_news(title, summary, source_name)

    if category == "bangladesh":
        prefix = "বাংলাদেশ আপডেট:"
    elif category == "world":
        prefix = "বিশ্ব আপডেট:"
    else:
        prefix = "টেক আপডেট:"

    return f"{prefix} {translated}"


def build_pending_caption(item):
    title = strip_html(item["title"])
    summary = strip_html(item["summary"])
    source_name = strip_html(item["source_name"])
    link = item["link"]
    idx = item.get("pending_index", "?")
    mode = item.get("mode", "review")

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

    return (
        f"{header}\n"
        f"ID: {idx}\n"
        f"Mode: {mode}\n\n"
        f"Title: {title}\n\n"
        f"{make_bangla_summary(title, summary, source_name)}\n\n"
        f"Source: {source_name}\n"
        f"{link}\n\n"
        f"Action:\n"
        f"/approve {idx}\n"
        f"/reject {idx}\n"
        f"/skip {idx}\n"
        f"/editcaption {idx} তোমার নতুন caption\n\n"
        f"Reply করেও /approve বা /skip দিতে পারো।"
    )


def build_public_caption(item):
    if item.get("custom_caption"):
        return item["custom_caption"]

    title = strip_html(item["title"])
    summary = strip_html(item["summary"])
    source_name = strip_html(item["source_name"])
    link = item["link"]
    bangla_summary = make_bangla_summary(title, summary, source_name)

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
        f"{bangla_summary}\n\n"
        f"🌐 Source: {source_name}\n"
        f"🔗 {link}"
    )


def generate_reel_script(item):
    title = strip_html(item["title"])
    summary = strip_html(item["summary"])
    short_summary = shorten_text(strip_html(summary), 220)

    return (
        "🎥 REELS SCRIPT\n\n"
        f"Hook:\nআজকের সবচেয়ে বড় খবর — {title}\n\n"
        f"Body:\n{short_summary}\n\n"
        f"CTA:\nআরও এমন আপডেট পেতে follow করুন।"
    )


def format_pending_list(items):
    if not items:
        return "কোনো pending news নেই।"

    lines = ["🟡 Pending approvals:\n"]
    for idx, item in items:
        title = shorten_text(strip_html(item.get("title", "")), 90)
        source = strip_html(item.get("source_name", "Unknown"))
        mode = item.get("mode", "review")
        lines.append(f"{idx}. [{mode}] {title}\n   Source: {source}")

    return "\n".join(lines[:40])


def format_approved_list(items):
    if not items:
        return "এখনও কোনো approved item নেই।"

    latest = list(reversed(items))[:20]
    lines = ["✅ Approved items:\n"]

    for idx, item in enumerate(latest, start=1):
        title = shorten_text(strip_html(item.get("title", "")), 90)
        source = strip_html(item.get("source_name", "Unknown"))
        approved_at = item.get("approved_at", "")[:19].replace("T", " ")
        lines.append(f"{idx}. {title}\n   Source: {source}\n   At: {approved_at}")

    return "\n".join(lines)


# =========================================================
# MEDIA
# =========================================================
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


# =========================================================
# FEED FETCH
# =========================================================
def fetch_feed_entries(source_name: str, source_url: str):
    headers = {"User-Agent": "Mozilla/5.0 NewsBot/2.0"}

    response = requests.get(source_url, timeout=25, headers=headers)
    response.raise_for_status()

    parsed = feedparser.parse(response.content)
    return getattr(parsed, "entries", [])


def source_requires_review(source: dict, title: str, summary: str):
    if source.get("mode") == "review":
        return True

    src_name = source.get("name", "").lower()
    url = source.get("url", "").lower()

    if "facebook" in src_name or "facebook" in url:
        return True

    if len(strip_html(summary)) < 60:
        return True

    if contains_any(f"{title} {summary}", ["rumor", "unverified", "claim", "viral"]):
        return True

    return False


def fetch_candidates():
    seen = load_seen()
    seen_links = {x.get("link", "") for x in seen}
    seen_titles = [x.get("title", "") for x in seen]

    queue = load_queue()
    pending_titles = [x.get("title", "") for x in queue if x.get("status") == "pending"]
    pending_links = {x.get("link", "") for x in queue if x.get("status") == "pending"}

    out = []
    debug_errors = []
    sources = load_sources()

    for source in sources:
        if not isinstance(source, dict):
            continue

        source = normalize_source(source)
        source_name = source.get("name", "").strip()
        source_url = source.get("url", "").strip()
        source_type = source.get("type", "feed").strip().lower()
        enabled = source.get("enabled", True)

        if not enabled:
            continue
        if not source_name or not source_url:
            continue
        if source_type != "feed":
            continue

        if "your-facebook-feed-or-bridge-url" in source_url:
            mark_source_result(source_name, ok=False, error="Placeholder URL detected")
            debug_errors.append(f"{source_name}: placeholder URL disabled")
            continue

        try:
            entries = fetch_feed_entries(source_name, source_url)
            mark_source_result(source_name, ok=True)
        except Exception as e:
            error_text = str(e)
            print(f"[ERROR] Source failed: {source_name} -> {error_text}")
            mark_source_result(source_name, ok=False, error=error_text)
            debug_errors.append(f"{source_name}: {error_text}")
            continue

        for entry in entries[:12]:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            summary = strip_html(raw_summary)

            if not title or not link:
                continue
            if link in seen_links or link in pending_links:
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
                "mode": "review" if source_requires_review(source, title, summary) else "auto",
                "source_slug": source.get("slug"),
                "created_at": now_iso(),
                "approved_at": None,
                "rejected_at": None,
                "skipped_at": None,
            })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:MAX_PENDING_PER_RUN], debug_errors


# =========================================================
# FB PUBLISH
# =========================================================
def fb_post_text(message: str, link: str):
    if not (FB_ENABLE_PUBLISH and FB_PAGE_ID and FB_PAGE_TOKEN):
        return

    url = f"https://graph.facebook.com/{FB_PAGE_ID}/feed"
    payload = {
        "message": message,
        "link": link,
        "access_token": FB_PAGE_TOKEN
    }
    response = requests.post(url, data=payload, timeout=60)
    response.raise_for_status()


def fb_post_photo(file_path: str, caption: str):
    if not (FB_ENABLE_PUBLISH and FB_PAGE_ID and FB_PAGE_TOKEN):
        return

    url = f"https://graph.facebook.com/{FB_PAGE_ID}/photos"
    with open(file_path, "rb") as f:
        response = requests.post(
            url,
            data={"caption": caption, "access_token": FB_PAGE_TOKEN},
            files={"source": f},
            timeout=120
        )
        response.raise_for_status()


def fb_post_video(file_path: str, caption: str):
    if not (FB_ENABLE_PUBLISH and FB_PAGE_ID and FB_PAGE_TOKEN):
        return

    url = f"https://graph.facebook.com/{FB_PAGE_ID}/videos"
    with open(file_path, "rb") as f:
        response = requests.post(
            url,
            data={"description": caption, "access_token": FB_PAGE_TOKEN},
            files={"source": f},
            timeout=300
        )
        response.raise_for_status()


# =========================================================
# PUBLISH
# =========================================================
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

    if FB_ENABLE_PUBLISH:
        fb_post_text(caption, item["link"])


# =========================================================
# COMMAND HELPERS
# =========================================================
def extract_index_from_text(text: str, cmd_name: str):
    raw = (text or "").strip()
    raw = re.sub(rf"^/{cmd_name}(?:@\w+)?", "", raw).strip()
    if raw.isdigit():
        return int(raw)
    return None


def extract_index_and_text(text: str, cmd_name: str):
    raw = (text or "").strip()
    raw = re.sub(rf"^/{cmd_name}(?:@\w+)?", "", raw).strip()
    if not raw:
        return None, ""

    match = re.match(r"^(\d+)\s+(.+)$", raw, flags=re.S)
    if not match:
        return None, raw

    return int(match.group(1)), match.group(2).strip()


async def update_admin_pending_message(bot, item):
    if not item.get("admin_message_id"):
        return

    try:
        caption = build_pending_caption(item)
        if item.get("image_url"):
            await bot.edit_message_caption(
                chat_id=ADMIN_CHAT_ID,
                message_id=item["admin_message_id"],
                caption=caption[:1024],
            )
        else:
            await bot.edit_message_text(
                chat_id=ADMIN_CHAT_ID,
                message_id=item["admin_message_id"],
                text=caption,
            )
    except Exception:
        pass


# =========================================================
# COMMANDS
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    bot_name = context.bot.username or "YourBot"

    await update.message.reply_text(
        "✅ Bot is running.\n\n"
        "Main commands:\n"
        f"/fetchnow@{bot_name}\n"
        f"/listsources@{bot_name}\n"
        f"/pending@{bot_name}\n"
        f"/review@{bot_name}\n"
        f"/approved@{bot_name}\n"
        f"/status@{bot_name}\n"
        f"/sourceerrors@{bot_name}\n\n"
        "Approval:\n"
        f"/approve@{bot_name} 1\n"
        f"/reject@{bot_name} 1\n"
        f"/skip@{bot_name} 1\n"
        f"/editcaption@{bot_name} 1 তোমার নতুন caption\n\n"
        "Reply mode:\n"
        "Pending post-এ reply করে /approve বা /skip দাও।"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    queue = load_queue()
    pending = [x for x in queue if x.get("status") == "pending"]
    approved = [x for x in queue if x.get("status") == "approved"]
    rejected = [x for x in queue if x.get("status") == "rejected"]
    skipped = [x for x in queue if x.get("status") == "skipped"]

    sources = load_sources()
    enabled = [s for s in sources if s.get("enabled", True)]
    disabled = [s for s in sources if not s.get("enabled", True)]

    await update.message.reply_text(
        "📊 Queue status:\n"
        f"Pending: {len(pending)}\n"
        f"Approved: {len(approved)}\n"
        f"Rejected: {len(rejected)}\n"
        f"Skipped: {len(skipped)}\n"
        f"Sources enabled: {len(enabled)}\n"
        f"Sources disabled: {len(disabled)}\n"
        f"Facebook publish: {'ON' if FB_ENABLE_PUBLISH else 'OFF'}"
    )


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    items = next_pending_indexed()
    await update.message.reply_text(format_pending_list(items))


async def review_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    index = extract_index_from_text(update.message.text or "", "review")

    if index is None:
        items = next_pending_indexed()
        await update.message.reply_text(format_pending_list(items))
        return

    item = find_pending_by_index(index)
    if not item:
        await update.message.reply_text("এই ID-এর pending item পাওয়া যায়নি।")
        return

    item["pending_index"] = index
    text = build_pending_caption(item)

    if item.get("image_url"):
        await update.message.reply_photo(photo=item["image_url"], caption=text[:1024])
    else:
        await update.message.reply_text(text)


async def approved_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    items = get_approved_items()
    await update.message.reply_text(format_approved_list(items))


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    queue = load_queue()
    item = None

    if update.message and update.message.reply_to_message:
        item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
    else:
        index = extract_index_from_text(update.message.text or "", "approve")
        if index is not None:
            item = find_pending_by_index(index)

    if not item:
        await update.message.reply_text("Pending item পাইনি। Use: /review or /approve 1")
        return

    try:
        await publish_item(context.application, item)
        item["status"] = "approved"
        item["approved_at"] = now_iso()
        save_queue(queue)
        add_seen_item(item)

        await update.message.reply_text("✅ Approved and posted.")
        await update.message.reply_text(generate_reel_script(item))
        await update_admin_pending_message(context.bot, item)
    except Exception as e:
        await update.message.reply_text(f"Approve failed: {e}")


async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    queue = load_queue()
    item = None

    if update.message and update.message.reply_to_message:
        item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
    else:
        index = extract_index_from_text(update.message.text or "", "reject")
        if index is not None:
            item = find_pending_by_index(index)

    if not item:
        await update.message.reply_text("Pending item পাইনি। Use: /review 1")
        return

    item["status"] = "rejected"
    item["rejected_at"] = now_iso()
    save_queue(queue)
    add_seen_item(item)

    await update.message.reply_text("❌ Rejected.")
    await update_admin_pending_message(context.bot, item)


async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    queue = load_queue()
    item = None

    if update.message and update.message.reply_to_message:
        item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
    else:
        index = extract_index_from_text(update.message.text or "", "skip")
        if index is not None:
            item = find_pending_by_index(index)

    if not item:
        await update.message.reply_text("Pending item পাইনি। Use: /review 1")
        return

    item["status"] = "skipped"
    item["skipped_at"] = now_iso()
    save_queue(queue)
    add_seen_item(item)

    await update.message.reply_text("⏭️ Skipped.")
    await update_admin_pending_message(context.bot, item)


async def editcaption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    queue = load_queue()
    item = None
    new_caption = ""

    if update.message and update.message.reply_to_message:
        item = find_pending_by_reply(queue, update.message.reply_to_message.message_id)
        raw = update.message.text or ""
        new_caption = re.sub(r"^/editcaption(?:@\w+)?", "", raw).strip()
    else:
        idx, text = extract_index_and_text(update.message.text or "", "editcaption")
        if idx is not None:
            item = find_pending_by_index(idx)
            new_caption = text

    if not item:
        await update.message.reply_text("Pending item পাইনি। Use: /editcaption 1 তোমার caption")
        return

    if not new_caption:
        await update.message.reply_text("Use: /editcaption 1 তোমার নতুন caption")
        return

    item["custom_caption"] = new_caption
    save_queue(queue)
    await update.message.reply_text("✏️ Caption updated.")
    await update_admin_pending_message(context.bot, item)


async def addsource_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    raw = update.message.text or ""
    payload = re.sub(r"^/addsource(?:@\w+)?", "", raw).strip()

    if "|" not in payload:
        await update.message.reply_text("Use: /addsource Name | URL | auto অথবা review")
        return

    parts = [x.strip() for x in payload.split("|")]
    if len(parts) < 2:
        await update.message.reply_text("Use: /addsource Name | URL | auto অথবা review")
        return

    name = parts[0]
    url = parts[1]
    mode = parts[2].lower() if len(parts) >= 3 else "auto"

    if mode not in {"auto", "review"}:
        mode = "auto"

    sources = load_sources()
    sources.append({
        "name": name,
        "url": url,
        "type": "feed",
        "mode": mode,
        "enabled": True,
    })
    save_sources(sources)

    await update.message.reply_text(f"✅ Source added:\n{name}\n{url}\nMode: {mode}")


async def removesource_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    raw = update.message.text or ""
    name = re.sub(r"^/removesource(?:@\w+)?", "", raw).strip()
    if not name:
        await update.message.reply_text("Use: /removesource exact_source_name")
        return

    sources = load_sources()
    new_sources = [s for s in sources if s.get("name") != name]

    if len(new_sources) == len(sources):
        await update.message.reply_text("Source name not found.")
        return

    save_sources(new_sources)
    await update.message.reply_text(f"🗑️ Removed source: {name}")


async def enablesource_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    raw = update.message.text or ""
    name = re.sub(r"^/enablesource(?:@\w+)?", "", raw).strip()
    if not name:
        await update.message.reply_text("Use: /enablesource exact_source_name")
        return

    sources = load_sources()
    found = False

    for src in sources:
        if src.get("name") == name:
            src["enabled"] = True
            src["fail_count"] = 0
            src["last_error"] = ""
            found = True
            break

    if not found:
        await update.message.reply_text("Source name not found.")
        return

    save_sources(sources)
    await update.message.reply_text(f"✅ Enabled source: {name}")


async def disablesource_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    raw = update.message.text or ""
    name = re.sub(r"^/disablesource(?:@\w+)?", "", raw).strip()
    if not name:
        await update.message.reply_text("Use: /disablesource exact_source_name")
        return

    sources = load_sources()
    found = False

    for src in sources:
        if src.get("name") == name:
            src["enabled"] = False
            found = True
            break

    if not found:
        await update.message.reply_text("Source name not found.")
        return

    save_sources(sources)
    await update.message.reply_text(f"⛔ Disabled source: {name}")


async def resetsources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    reset_sources_to_default()
    await update.message.reply_text("✅ Sources reset to DEFAULT_SOURCES.")


async def listsources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    sources = load_sources()
    if not sources:
        await update.message.reply_text("No sources found.")
        return

    lines = []
    for s in sources[:100]:
        status = "ON" if s.get("enabled", True) else "OFF"
        mode = s.get("mode", "auto")
        fail_count = s.get("fail_count", 0)
        lines.append(
            f"- {s.get('name')} [{status}] [{mode}] [fails:{fail_count}]\n"
            f"{s.get('url')}"
        )

    await update.message.reply_text("Current sources:\n\n" + "\n".join(lines[:100]))


async def sourceerrors_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    sources = load_sources()
    bad = [s for s in sources if s.get("last_error")]

    if not bad:
        await update.message.reply_text("No source errors.")
        return

    lines = []
    for s in bad[:30]:
        lines.append(
            f"- {s.get('name')}\n"
            f"  enabled: {s.get('enabled')}\n"
            f"  fails: {s.get('fail_count', 0)}\n"
            f"  error: {shorten_text(s.get('last_error', ''), 180)}"
        )

    await update.message.reply_text("Source errors:\n\n" + "\n\n".join(lines))


async def fetchnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        return

    await update.message.reply_text("⏳ Fetching now...")
    try:
        added, auto_posted, debug = await collect_now(context.application)
        msg = f"✅ Fetch done.\nAdded to pending: {added}\nAuto posted: {auto_posted}"
        if debug:
            msg += f"\n\nDebug:\n{debug[:3000]}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"❌ Fetch failed: {e}")


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
        await update.message.reply_text("🎬 Edited video attached.")
        return

    if update.message.photo:
        item["attached_type"] = "photo"
        item["attached_file_id"] = update.message.photo[-1].file_id
        save_queue(queue)
        await update.message.reply_text("🖼️ Edited photo attached.")
        return


# =========================================================
# COLLECT
# =========================================================
async def collect_now(app: Application):
    candidates, source_errors = fetch_candidates()
    queue = load_queue()
    added = 0
    auto_posted = 0
    debug_lines = [f"Candidates found: {len(candidates)}"]

    for cand in candidates:
        if cand.get("mode") == "auto":
            try:
                await publish_item(app, cand)
                cand["status"] = "approved"
                cand["approved_at"] = now_iso()
                queue.append(cand)
                add_seen_item(cand)
                auto_posted += 1
                debug_lines.append(f"Auto posted: {cand['source_name']} -> {cand['title'][:80]}")
                continue
            except Exception as e:
                cand["mode"] = "review"
                debug_lines.append(f"Auto post fallback to review: {cand['source_name']} -> {str(e)[:120]}")

        pending_count_now = len([x for x in queue if x.get("status") == "pending"]) + 1
        cand["pending_index"] = pending_count_now
        text = build_pending_caption(cand)

        if cand.get("image_url"):
            sent = await app.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=cand["image_url"], caption=text[:1024])
        else:
            sent = await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

        cand["admin_message_id"] = sent.message_id
        queue.append(cand)
        added += 1
        debug_lines.append(f"Added to review: {cand['source_name']} -> {cand['title'][:80]}")

    if source_errors:
        debug_lines.append("")
        debug_lines.append("Source errors:")
        debug_lines.extend(source_errors[:20])

    save_queue(queue)
    return added, auto_posted, "\n".join(debug_lines)


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
                added, auto_posted, debug = await collect_now(app)
                mark_collected(slot_key)
                print(f"[COLLECTED] pending={added}, auto={auto_posted}")
                print(debug)
            else:
                print("[WAIT] not collection window")

        except Exception as e:
            print(f"[COLLECTOR ERROR] {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(app: Application):
    app.create_task(collector_loop(app))


# =========================================================
# MAIN
# =========================================================
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")
    if not PUBLIC_CHANNEL_ID:
        raise ValueError("PUBLIC_CHANNEL_ID is missing")
    if not ADMIN_CHAT_ID:
        raise ValueError("ADMIN_CHAT_ID is missing or invalid")

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
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("review", review_cmd))
    app.add_handler(CommandHandler("approved", approved_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("editcaption", editcaption_cmd))
    app.add_handler(CommandHandler("addsource", addsource_cmd))
    app.add_handler(CommandHandler("removesource", removesource_cmd))
    app.add_handler(CommandHandler("enablesource", enablesource_cmd))
    app.add_handler(CommandHandler("disablesource", disablesource_cmd))
    app.add_handler(CommandHandler("resetsources", resetsources_cmd))
    app.add_handler(CommandHandler("listsources", listsources_cmd))
    app.add_handler(CommandHandler("sourceerrors", sourceerrors_cmd))
    app.add_handler(CommandHandler("fetchnow", fetchnow_cmd))
    app.add_handler(MessageHandler((filters.PHOTO | filters.VIDEO), media_attach_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
