#!/usr/bin/env python3
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import feedparser
from anthropic import Anthropic

# ============================================================
# CONFIGURATION
# ============================================================

# Environment variables (set in .env or .bashrc)
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SENDER = os.environ.get("BRIEFING_SENDER", SMTP_USER)

RECIPIENT = "lewis@i2capmark.com"
MODEL = "claude-3-5-sonnet-20241022"
MAX_TOKENS = 4000

# Public RSS feeds (safe, non‑paywalled)
FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=&region=US&lang=en-US",
    "https://www.marketwatch.com/rss/topstories",
    "https://www.businesswire.com/portal/site/home/news/rss",
    "https://www.globenewswire.com/RssFeed/org-category/Finance.xml",
    "https://home.treasury.gov/news/press-releases/rss",  # OFAC / US Treasury
]

# Keywords relevant to distressed debt / restructuring
KEYWORDS = [
    "default", "distressed", "restructuring", "chapter 11", "scheme of arrangement",
    "consent solicitation", "tender offer", "exchange offer", "missed coupon",
    "bondholders", "RMBS", "mortgage-backed", "sovereign debt", "haircut",
    "Air Baltic", "Nord Stream", "Schleich", "Sound Energy", "Paratus AMC",
    "Goldman Sachs", "Morgan Stanley", "BlackRock", "Intesa Sanpaolo",
    "Kroll", "GLAS"
]


# ============================================================
# FETCH ARTICLES
# ============================================================

def fetch_articles():
    """Fetch and filter articles from public RSS feeds."""
    articles = []
    since = datetime.utcnow() - timedelta(days=1)

    for url in FEEDS:
        feed = feedparser.parse(url)

        for entry in feed.entries:
            # Filter by publication date (RSS formats vary)
            published = getattr(entry, "published_parsed", None)
            if published:
                pub_dt = datetime(*published[:6])
                if pub_dt < since:
                    continue

            title = entry.get("title", "")
            summary = entry.get("summary", "")
            link = entry.get("link", "")

            combined_text = f"{title} {summary}".lower()
            if any(kw.lower() in combined_text for kw in KEYWORDS):
                articles.append({
                    "title": title,
                    "summary": summary,
                    "link": link
                })

    return articles


# ============================================================
# BUILD PROMPT FOR CLAUDE
# ============================================================

def build_prompt(articles):
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Convert articles into readable text blocks
    articles_text = "\n".join(
        f"- {a['title']}\n  {a['summary']}\n  Source: {a['link']}"
        for a in articles
    ) or "No matching articles found in the last 24 hours."

    system_prompt = (
        "You are preparing a distressed-debt capital markets briefing for i2 Capital Markets, "
        "a trust and agency provider (loan/security agency, bond trusteeship, consent solicitations, "
        "liability management, escrow). Use only the article data provided; do not invent sources or links."
    )

    user_prompt = f"""
Daily Distressed Debt / DCM Briefing for i2 Capital Markets – {today}

Task:
Produce a concise, professional briefing using ONLY the articles listed below (public sources).
Focus on:
- Distressed debt / restructuring situations
- Sovereign or corporate bond defaults, missed coupons, covenant breaches
- Debt exchanges, consent solicitations, tender/exchange offers
- Chapter 11 / schemes / arrangements / examinerships
- Named i2 situations: Air Baltic, Nord Stream, Schleich, Sound Energy, Paratus AMC, RMBS deals involving GS, MS, BlackRock, Intesa
- Competitor activity: Kroll, GLAS
- Clearing system / corporate actions changes
- Sanctions / OFAC items relevant to cross-border or distressed debt

Articles:
{articles_text}

Output format:
For each relevant item:
- Headline
- One-line summary
- Why it matters to i2 (which service line: agency, trusteeship, consent solicitation, liability management, escrow)
- Source + link

If a category has no relevant items, say: "No relevant items in this category today."

After listing items, provide a short "What Matters Today" section (3–5 bullets) summarising key implications for i2.

Then draft an email body to lewis@i2capmark.com with subject:
"i2 Morning News Briefing – {today}"
containing the full briefing in clean, professional prose.
"""

    return system_prompt, user_prompt


# ============================================================
# CALL CLAUDE API
# ============================================================

def generate_briefing(system_prompt, user_prompt):
    if not CLAUDE_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = Anthropic(api_key=CLAUDE_API_KEY)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    # Extract text blocks
    output = []
    for block in response.content:
        if block.type == "text":
            output.append(block.text)

    return "\n".join(output)


# ============================================================
# SEND EMAIL
# ============================================================

def send_email(subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = RECIPIENT

    context = ssl.create_default_context()

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


# ============================================================
# MAIN
# ============================================================

def main():
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        raise RuntimeError("SMTP configuration missing")

    articles = fetch_articles()
    system_prompt, user_prompt = build_prompt(articles)
    briefing = generate_briefing(system_prompt, user_prompt)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    subject = f"i2 Morning News Briefing – {today}"

    send_email(subject, briefing)


if __name__ == "__main__":
    main()
