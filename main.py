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

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "")

TIMEZONE = "Asia/Dhaka"
POST_HOURS = [9, 20]  # morning & evening
CHECK_INTERVAL = 300
MAX_POST = 2

DATA_FILE = "posted.json"

RSS_FEEDS = [
    ("Prothom Alo", "https://www.prothomalo.com/feed"),
    ("BDNews24", "https://bdnews24.com/feed/"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
]

# ================= UTILS =================

def load_data():
    if os.path.exists(DATA_FILE):
        return json.load(open(DATA_FILE, "r", encoding="utf-8"))
    return []

def save_data(data):
    json.dump(data, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False)

def clean(text):
    text = re.sub(r"<.*?>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()

def normalize(text):
    return re.sub(r'[^a-z0-9\u0980-\u09FF ]', '', text.lower())

# ================= SMART FILTER =================

def is_duplicate(title, old_titles):
    t = normalize(title)
    for o in old_titles:
        if t in normalize(o) or normalize(o) in t:
            return True
    return False

def is_valid(title, summary):
    text = f"{title} {summary}".lower()

    # Bangladesh
    if "bangladesh" in text or "বাংলাদেশ" in text:
        return True

    # war/global
    if any(x in text for x in ["war","iran","usa","china","attack"]):
        return True

    # tech
    if any(x in text for x in ["ai","robot","iphone","android","gadget"]):
        return True

    return False

# ================= VIRAL SCORE =================

def score(title, summary):
    s = 0
    text = f"{title} {summary}".lower()

    if "bangladesh" in text: s += 3
    if "ai" in text: s += 3
    if "war" in text: s += 4
    if "iphone" in text or "android" in text: s += 2

    return s

# ================= BANGLA =================

def to_bangla(text):
    try:
        return GoogleTranslator(source="auto", target="bn").translate(text)
    except:
        return text

# ================= CAPTION =================

def caption(title, summary, link):
    bangla = to_bangla(summary[:200])

    return (
        f"🔥 <b>আজকের গুরুত্বপূর্ণ আপডেট</b>\n\n"
        f"📰 <b>{html.escape(title)}</b>\n\n"
        f"💡 {html.escape(bangla)}\n\n"
        f"👉 বিস্তারিত:\n{link}"
    )

# ================= SEND =================

def send_text(msg):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
        "chat_id": CHANNEL_USERNAME,
        "text": msg,
        "parse_mode": "HTML"
    })

def send_photo(url, msg):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={
        "chat_id": CHANNEL_USERNAME,
        "photo": url,
        "caption": msg[:1024],
        "parse_mode": "HTML"
    })

def send_video(url, msg):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={
        "chat_id": CHANNEL_USERNAME,
        "video": url,
        "caption": msg[:1024],
        "parse_mode": "HTML"
    })

# ================= MEDIA =================

def get_media(entry):
    # video check
    for link in entry.get("links", []):
        if "video" in link.get("type",""):
            return ("video", link.get("href"))

    # image check
    summary = entry.get("summary","")
    match = re.search(r'<img[^>]+src="([^"]+)"', summary)
    if match:
        return ("image", match.group(1))

    return (None, None)

# ================= FETCH =================

def fetch():
    items = []

    for name, url in RSS_FEEDS:
        feed = feedparser.parse(url)

        for e in feed.entries[:8]:
            title = e.get("title","")
            link = e.get("link","")
            summary = clean(e.get("summary",""))

            if not is_valid(title, summary):
                continue

            media_type, media_url = get_media(e)

            items.append({
                "title": title,
                "link": link,
                "summary": summary,
                "media_type": media_type,
                "media_url": media_url,
                "score": score(title, summary)
            })

    items.sort(key=lambda x: x["score"], reverse=True)
    return items

# ================= POST =================

def post():
    data = load_data()
    old_titles = [d["title"] for d in data]

    items = fetch()
    count = 0

    for item in items:
        if count >= MAX_POST:
            break

        if is_duplicate(item["title"], old_titles):
            continue

        msg = caption(item["title"], item["summary"], item["link"])

        try:
            if item["media_type"] == "video":
                send_video(item["media_url"], msg)
            elif item["media_type"] == "image":
                send_photo(item["media_url"], msg)
            else:
                send_text(msg)

            data.append({"title": item["title"], "link": item["link"]})
            save_data(data)

            count += 1
            time.sleep(3)

        except Exception as e:
            print("Error:", e)

    if count == 0:
        print("No new news")

# ================= SCHEDULE =================

def main():
    print("🚀 BOT RUNNING...")

    while True:
        now = datetime.now(ZoneInfo(TIMEZONE))
        print("Time:", now)

        if now.hour in POST_HOURS:
            post()
            time.sleep(3600)
        else:
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()