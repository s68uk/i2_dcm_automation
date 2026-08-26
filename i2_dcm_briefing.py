#!/usr/bin/env python3
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ---------------------------------------------------------
# Helper: Extract RSS feed items
# ---------------------------------------------------------
def extract_feed(url, source, limit=10):
    feed = feedparser.parse(url)
    items = []

    for entry in feed.entries[:limit]:
        items.append({
            "source": source,
            "title": entry.get("title", "No title"),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
        })
    return items

# ---------------------------------------------------------
# Helper: Extract headline from webpage (fallback)
# ---------------------------------------------------------
def extract_webpage_title(url):
    try:
        r = requests.get(url, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.find("title")
        return title.text.strip() if title else "No title found"
    except Exception:
        return "No title found"

# ---------------------------------------------------------
# Build briefing sections
# ---------------------------------------------------------
def build_section(items):
    lines = []
    for item in items:
        published = item["published"] if item["published"] else "No timestamp"
        lines.append(
            f"{item['source']}: {item['title']} ({published})\n{item['link']}\n"
        )
    return "\n".join(lines)

# ---------------------------------------------------------
# Main briefing generator
# ---------------------------------------------------------
def generate_briefing():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # RSS feeds
    reuters_items = extract_feed(
        "https://feeds.reuters.com/reuters/UKdomesticNews",
        "Reuters"
    )
    ft_items = extract_feed(
        "https://www.ft.com/?format=rss",
        "Financial Times"
    )
    bloomberg_items = extract_feed(
        "https://www.bloomberg.com/feeds/rss/markets",
        "Bloomberg"
    )

    # Build briefing text
    briefing = []
    briefing.append(f"DCM Briefing — {now}\n")
    briefing.append("=" * 60 + "\n")

    briefing.append("REUTERS\n")
    briefing.append(build_section(reuters_items))
    briefing.append("\n" + "=" * 60 + "\n")

    briefing.append("FINANCIAL TIMES\n")
    briefing.append(build_section(ft_items))
    briefing.append("\n" + "=" * 60 + "\n")

    briefing.append("BLOOMBERG\n")
    briefing.append(build_section(bloomberg_items))
    briefing.append("\n" + "=" * 60 + "\n")

    return "\n".join(briefing)

# ---------------------------------------------------------
# Write briefing to file
# ---------------------------------------------------------
def main():
    briefing_text = generate_briefing()
    output_path = "/home/s68uk/i2_dcm_automation/briefing.txt"

    with open(output_path, "w") as f:
        f.write(briefing_text)

    print("Briefing generated:", output_path)

# ---------------------------------------------------------
if __name__ == "__main__":
    main()
