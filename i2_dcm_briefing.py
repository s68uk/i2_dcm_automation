#!/usr/bin/env python3
import feedparser
from datetime import datetime

# ---------------------------------------------------------
# Feed list (DCM‑relevant sources only)
# ---------------------------------------------------------
FEEDS = [
    ("https://www.marketwatch.com/rss/bonds", "MarketWatch Bonds"),
    ("https://www.investing.com/rss/news_25.rss", "Investing.com Bonds"),
    ("https://www.businesswire.com/portal/site/home/rss/", "BusinessWire"),
    ("https://www.globenewswire.com/RssFeed/industry/Financial%20Services", "GlobeNewswire"),
    ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=rss", "SEC Filings")
]

# ---------------------------------------------------------
# Extract items from a feed
# ---------------------------------------------------------
def extract_feed(url, source, limit=10):
    feed = feedparser.parse(url)
    items = []

    for entry in feed.entries[:limit]:
        items.append({
            "source": source,
            "title": entry.get("title", "No title"),
            "link": entry.get("link", ""),
            "published": entry.get("published", "")
        })
    return items

# ---------------------------------------------------------
# Build a section for one source
# ---------------------------------------------------------
def build_section(source, items):
    if not items:
        return f"{source}\n(No items found)\n"

    lines = [f"{source}\n"]
    for item in items:
        published = item["published"] if item["published"] else "No timestamp"
        lines.append(f"- {item['title']} ({published})")
        lines.append(f"  {item['link']}\n")
    return "\n".join(lines)

# ---------------------------------------------------------
# Main briefing generator
# ---------------------------------------------------------
def generate_briefing():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    briefing = [f"DCM Briefing — {now}\n", "=" * 60 + "\n"]

    for url, source in FEEDS:
        items = extract_feed(url, source)
        section = build_section(source, items)
        briefing.append(section)
        briefing.append("\n" + "=" * 60 + "\n")

    return "\n".join(briefing)

# ---------------------------------------------------------
# Write briefing to file
# ---------------------------------------------------------
def main():
    output_path = "/home/s68uk/i2_dcm_automation/briefing.txt"
    briefing_text = generate_briefing()

    with open(output_path, "w") as f:
        f.write(briefing_text)

    print("Briefing generated:", output_path)

# ---------------------------------------------------------
if __name__ == "__main__":
    main()
