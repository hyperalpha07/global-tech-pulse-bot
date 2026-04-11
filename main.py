import os
import re
import json
import time
import html
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import feedparser
from deep_translator import GoogleTranslator

# =========================
# ENV VARIABLES
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()

FB_TOKEN = os.getenv("FB_TOKEN", "").strip()
PAGE_ID = os.getenv("PAGE_ID", "").strip()

TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka").strip()
POST_HOURS_RAW = os.getenv("POST_HOURS", "9,20").strip()
MAX_POSTS_PER_SLOT = int(os.getenv("MAX_POSTS_PER_SLOT", "2"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

# =========================
# FILES
# =========================
POSTED_FILE = "posted_data.json"
SCHEDULE_FILE = "schedule_state.json"

# =========================
# RSS FEEDS
# =========================
RSS_FEEDS = [
    # Bangladesh
    ("Prothom Alo", "https://www.prothomalo.com/feed"),
    ("BDNews24", "https://bdnews24.com/feed/"),
    ("Bangla Tribune", "https://banglatribune.com/feed/"),
    ("The Daily Star", "https://www.thedailystar.net/frontpage/rss.xml"),

    # Global important + tech
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Wired", "https://www.wired.com/feed/rss"),
]

# =========================
# KEYWORDS
# =========================
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
    "coupon", "discount", "conference pass", "ticket", "sale ends", "subscribe now",
    "investor presentation", "earnings call", "quarterly report", "shareholder", "promo"
]

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


def load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def strip_html(raw_text):
    text = re.sub(r"<.*?>", "", raw_text or "")
    text = text.replace("\n", " ").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def shorten_text(text, limit=260):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + "..."


def normalize_text(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\u0980-\u09FF ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_similar_news(new_title, old_titles):
    new_norm = normalize_text(new_title)

    for old in old_titles:
        old_norm = normalize_text(old)

        if not new_norm or not old_norm:
            continue

        if new_norm == old_norm:
            return True

        if new_norm in old_norm or old_norm in new_norm:
            return True

    return False


def contains_any(text, keywords):
    text = (text or "").lower()
    return any(word in text for word in keywords)


# =========================
# NEWS FILTERING
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
    category = classify_news(title, summary, source_name)
    return category in ["bangladesh", "world", "tech"]


def is_breaking_news(title, summary):
    text = f"{title} {summary}".lower()
    return contains_any(text, [
        "breaking", "urgent", "war", "attack", "missile", "iran",
        "usa", "israel", "china", "russia", "crisis", "earthquake"
    ])


def score_news(title, summary, source_name):
    text = f"{title} {summary} {source_name}".lower()
    score = 0

    category = classify_news(title, summary, source_name)

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


# =========================
# TRANSLATION / BANGLA SUMMARY
# =========================
def to_bangla(text):
    text = (text or "").strip()
    if not text:
        return ""

    try:
        translated = GoogleTranslator(source="auto", target="bn").translate(text)
        return translated.strip()
    except Exception:
        return text


def make_bangla_summary(title, summary, source_name):
    base = f"{title}. {shorten_text(summary, 220)}"
    translated = to_bangla(base)
    translated = shorten_text(translated, 280)

    category = classify_news(title, summary, source_name)

    if category == "bangladesh":
        prefix = "বাংলাদেশ আপডেট:"
    elif category == "world":
        prefix = "বিশ্বের গুরুত্বপূর্ণ খবর:"
    elif category == "tech":
        prefix = "টেক আপডেট:"
    else:
        prefix = "আজকের খবর:"

    return f"{prefix} {translated}"


# =========================
# CAPTION
# =========================
def build_caption(title, summary, source_name, link):
    bangla_summary = make_bangla_summary(title, summary, source_name)

    if is_breaking_news(title, summary):
        header = "🚨 ব্রেকিং নিউজ"
    else:
        category = classify_news(title, summary, source_name)
        if category == "bangladesh":
            header = "🇧🇩 বাংলাদেশের গুরুত্বপূর্ণ আপডেট"
        elif category == "world":
            header = "🌍 বিশ্বের জরুরি খবর"
        elif category == "tech":
            header = "📱 AI / Gadget / Tech Update"
        else:
            header = "🔥 আজকের গুরুত্বপূর্ণ আপডেট"

    safe_header = html.escape(header)
    safe_title = html.escape(title.strip())
    safe_summary = html.escape(bangla_summary.strip())
    safe_source = html.escape(source_name.strip())
    safe_link = html.escape(link.strip())

    caption = (
        f"{safe_header}\n\n"
        f"📰 <b>{safe_title}</b>\n\n"
        f"💡 <b>বাংলা সারাংশ:</b>\n{safe_summary}\n\n"
        f"🔗 <b>Source:</b> {safe_source}\n"
        f"{safe_link}\n\n"
        f"📢 <b>আরও আপডেট পেতে join করুন:</b> {CHANNEL_USERNAME}"
    )
    return caption


# =========================
# FACEBOOK CAPTION
# =========================
def build_facebook_message(title, summary, source_name, link):
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
            header = "📱 Tech / AI Update"

    return (
        f"{header}\n\n"
        f"📰 {title}\n\n"
        f"💡 {bangla_summary}\n\n"
        f"🔗 Source: {source_name}\n"
        f"{link}\n\n"
        f"📢 Telegram: {CHANNEL_USERNAME}"
    )


# =========================
# MEDIA EXTRACTION
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


def extract_video(entry):
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        for item in media_content:
            media_url = item.get("url")
            media_type = item.get("type", "")
            if media_url and str(media_type).startswith("video/"):
                return media_url

    links = getattr(entry, "links", [])
    for item in links:
        href = item.get("href")
        media_type = item.get("type", "")
        if href and str(media_type).startswith("video/"):
            return href

    return None


# =========================
# TELEGRAM SEND
# =========================
def send_text_message(caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_USERNAME,
        "text": caption,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    response = requests.post(url, data=payload, timeout=60)
    response.raise_for_status()


def send_photo_message(photo_url, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHANNEL_USERNAME,
        "photo": photo_url,
        "caption": caption[:1024],
        "parse_mode": "HTML"
    }
    response = requests.post(url, data=payload, timeout=60)
    response.raise_for_status()


def send_video_message(video_url, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    payload = {
        "chat_id": CHANNEL_USERNAME,
        "video": video_url,
        "caption": caption[:1024],
        "parse_mode": "HTML"
    }
    response = requests.post(url, data=payload, timeout=60)
    response.raise_for_status()


# =========================
# FACEBOOK POST
# =========================
def post_to_facebook(message, link):
    if not FB_TOKEN or not PAGE_ID:
        print("[FB SKIPPED] FB_TOKEN or PAGE_ID missing.")
        return

    url = f"https://graph.facebook.com/{PAGE_ID}/feed"
    payload = {
        "message": message,
        "link": link,
        "access_token": FB_TOKEN
    }

    try:
        response = requests.post(url, data=payload, timeout=60)
        print("[FB RESPONSE]", response.text)
    except Exception as e:
        print("[FB ERROR]", e)


# =========================
# FETCH NEWS
# =========================
def fetch_candidates(posted_links):
    candidates = []

    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[ERROR] Feed parse failed for {source_name}: {e}")
            continue

        entries = getattr(feed, "entries", [])
        if not entries:
            print(f"[INFO] No entries found for {source_name}")
            continue

        for entry in entries[:12]:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            summary = strip_html(raw_summary)

            if not title or not link:
                continue

            if link in posted_links:
                continue

            if not is_valid_news(title, summary, source_name):
                continue

            image_url = extract_image(entry)
            video_url = extract_video(entry)

            item = {
                "title": title,
                "link": link,
                "summary": summary if summary else "Latest update from the source.",
                "source_name": source_name,
                "image_url": image_url,
                "video_url": video_url,
                "score": score_news(title, summary, source_name)
            }
            candidates.append(item)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


# =========================
# SCHEDULE
# =========================
def get_slot_key(now_dt):
    date_str = now_dt.strftime("%Y-%m-%d")
    hour_str = str(now_dt.hour)
    return f"{date_str}_{hour_str}"


def should_post_now():
    tz = ZoneInfo(TIMEZONE)
    now_dt = datetime.now(tz)

    if now_dt.hour not in POST_HOURS:
        return False, now_dt, None

    state = load_json_file(SCHEDULE_FILE, {})
    slot_key = get_slot_key(now_dt)

    if state.get("last_posted_slot") == slot_key:
        return False, now_dt, slot_key

    return True, now_dt, slot_key


def mark_slot_posted(slot_key):
    save_json_file(SCHEDULE_FILE, {"last_posted_slot": slot_key})


# =========================
# MAIN POST LOGIC
# =========================
def post_news_smart():
    posted_data = load_json_file(POSTED_FILE, [])
    posted_links = {item.get("link", "") for item in posted_data}
    posted_titles = [item.get("title", "") for item in posted_data]

    candidates = fetch_candidates(posted_links)

    if not candidates:
        print("[INFO] No candidates found.")
        return

    unique_candidates = []
    for item in candidates:
        title = item["title"]
        if is_similar_news(title, posted_titles):
            print(f"[SKIPPED SIMILAR] {title}")
            continue
        unique_candidates.append(item)

    if not unique_candidates:
        print("[INFO] No new unique news found. Skipping.")
        return

    posted_count = 0

    for item in unique_candidates:
        if posted_count >= MAX_POSTS_PER_SLOT:
            break

        title = item["title"]
        summary = item["summary"]
        source_name = item["source_name"]
        link = item["link"]
        image_url = item["image_url"]
        video_url = item["video_url"]

        telegram_caption = build_caption(title, summary, source_name, link)
        facebook_message = build_facebook_message(title, summary, source_name, link)

        try:
            if video_url:
                send_video_message(video_url, telegram_caption)
                print(f"[VIDEO POSTED] {title}")
            elif image_url:
                send_photo_message(image_url, telegram_caption)
                print(f"[PHOTO POSTED] {title}")
            else:
                send_text_message(telegram_caption)
                print(f"[TEXT POSTED] {title}")

            post_to_facebook(facebook_message, link)

            posted_data.append({
                "title": title,
                "link": link,
                "source": source_name,
                "posted_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat()
            })
            save_json_file(POSTED_FILE, posted_data)

            posted_count += 1
            time.sleep(4)

        except Exception as e:
            print(f"[ERROR] Failed to post '{title}': {e}")

    print(f"[DONE] Posted {posted_count} item(s).")


# =========================
# REELS SCRIPT GENERATOR
# =========================
def generate_reel_script(title, summary):
    short_summary = shorten_text(summary, 120)
    return (
        "🎥 REELS SCRIPT\n\n"
        "Hook:\n"
        f"আজকের সবচেয়ে বড় খবর — {title}\n\n"
        "Body:\n"
        f"{short_summary}\n\n"
        "CTA:\n"
        f"আরও এমন আপডেট পেতে Telegram channel join করুন: {CHANNEL_USERNAME}"
    )


# =========================
# MAIN LOOP
# =========================
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is missing.")
    if not CHANNEL_USERNAME:
        raise ValueError("CHANNEL_USERNAME environment variable is missing.")

    print("===================================")
    print("Bot started successfully...")
    print(f"Channel: {CHANNEL_USERNAME}")
    print(f"Timezone: {TIMEZONE}")
    print(f"Post hours: {POST_HOURS}")
    print(f"Max posts per slot: {MAX_POSTS_PER_SLOT}")
    print(f"Check interval: {CHECK_INTERVAL} seconds")
    print(f"Facebook enabled: {'YES' if FB_TOKEN and PAGE_ID else 'NO'}")
    print("===================================")

    while True:
        try:
            can_post, now_dt, slot_key = should_post_now()
            print(f"[CHECK] Now: {now_dt}")

            if can_post and slot_key:
                print(f"[POST WINDOW] Running scheduled posts for slot {slot_key}")
                post_news_smart()
                mark_slot_posted(slot_key)
            else:
                print("[WAIT] Not posting now or already posted in this slot.")

        except Exception as e:
            print(f"[LOOP ERROR] {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
