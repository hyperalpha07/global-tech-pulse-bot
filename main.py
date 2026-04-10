import os
import json
import time
import html
import requests
import feedparser

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()   # example: @globaltechpulse
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "600"))       # 600 sec = 10 min

RSS_FEEDS = [
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("Wired", "https://www.wired.com/feed/rss"),
]

POSTED_FILE = "posted_links.json"


def load_posted_links():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception:
            pass
    return set()


def save_posted_links(links):
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(links), f, ensure_ascii=False, indent=2)


def shorten_text(text, limit=300):
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + "..."


def clean_summary(summary):
    # Basic cleanup for HTML fragments inside RSS summaries
    summary = summary.replace("\n", " ").replace("\r", " ").strip()
    return shorten_text(summary, 300)


def build_message(title, summary, source_name, link):
    title = html.escape(title.strip())
    summary = html.escape(summary.strip())
    source_name = html.escape(source_name.strip())
    link = html.escape(link.strip())

    message = (
        f"📰 <b>{title}</b>\n\n"
        f"{summary}\n\n"
        f"🔗 <b>Source:</b> {source_name}\n"
        f"{link}"
    )
    return message


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_USERNAME,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_and_post_news():
    posted_links = load_posted_links()
    new_count = 0

    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[ERROR] Failed to parse feed {feed_url}: {e}")
            continue

        entries = getattr(feed, "entries", [])
        if not entries:
            print(f"[INFO] No entries found for {source_name}")
            continue

        for entry in entries[:5]:
            link = getattr(entry, "link", "").strip()
            title = getattr(entry, "title", "No title").strip()

            if not link or link in posted_links:
                continue

            raw_summary = getattr(entry, "summary", "")
            summary = clean_summary(raw_summary) if raw_summary else "Latest update from the source."

            message = build_message(title, summary, source_name, link)

            try:
                send_telegram_message(message)
                posted_links.add(link)
                save_posted_links(posted_links)
                new_count += 1
                print(f"[POSTED] {title}")
                time.sleep(3)
            except Exception as e:
                print(f"[ERROR] Telegram post failed: {e}")

    print(f"[DONE] Posted {new_count} new item(s).")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is missing.")
    if not CHANNEL_USERNAME:
        raise ValueError("CHANNEL_USERNAME environment variable is missing. Example: @globaltechpulse")

    print("Bot started successfully...")
    print(f"Posting to channel: {CHANNEL_USERNAME}")

    while True:
        try:
            fetch_and_post_news()
        except Exception as e:
            print(f"[LOOP ERROR] {e}")

        print(f"Sleeping for {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()