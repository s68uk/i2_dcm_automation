#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
from datetime import datetime

DISTRESSED_KEYWORDS = [
    "default", "missed coupon", "covenant breach", "restructuring",
    "chapter 11", "scheme of arrangement", "debt exchange",
    "tender offer", "consent solicitation", "liability management",
    "bondholder meeting", "rmbs", "mortgage-backed", "senior notes",
    "secured notes", "term loan"
]

NAMED_ISSUER_KEYWORDS = [
    "air baltic", "nord stream", "schleich", "sound energy",
    "paratus amc", "goldman sachs", "morgan stanley",
    "blackrock", "intesa sanpaolo"
]

LAW_FIRM_KEYWORDS = [
    "milbank", "kirkland & ellis", "linklaters", "a&o shearman",
    "akin gump", "latham & watkins", "gibson dunn"
]

ADVISER_COMPETITOR_KEYWORDS = [
    "houlihan lokey", "pjt partners", "fti consulting",
    "kroll", "glas"
]

CLEARING_SANCTIONS_KEYWORDS = [
    "euroclear", "clearstream", "dtc", "sanctions", "ofac"
]

SOURCES = [
    {
        "name": "Reuters Bankruptcy",
        "url": "https://www.reuters.com/business/bankruptcy/",
        "type": "news"
    },
    {
        "name": "OFAC Press Releases",
        "url": "https://home.treasury.gov/news/press-releases",
        "type": "sanctions"
    },
    {
        "name": "Kirkland & Ellis News",
        "url": "https://www.kirkland.com/news",
        "type": "law_firm"
    },
    {
        "name": "Houlihan Lokey Insights",
        "url": "https://hl.com/insights/",
        "type": "adviser"
    }
]


def fetch_html(url, timeout=8):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def extract_reuters_bankruptcy(html, source_name):
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for art in soup.find_all("article"):
        h = art.find("h3")
        a = art.find("a")
        if not h or not a or not a.get("href"):
            continue
        headline = h.get_text(strip=True)
        link = "https://www.reuters.com" + a.get("href")
        summary = ""
        p = art.find("p")
        if p:
            summary = p.get_text(strip=True)
        stories.append({
            "source": source_name,
            "headline": headline,
            "summary": summary,
            "link": link
        })
    return stories


def extract_ofac_press(html, source_name):
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.select("div.view-content div.views-row"):
        a = item.find("a")
        if not a or not a.get("href"):
            continue
        headline = a.get_text(strip=True)
        link = "https://home.treasury.gov" + a.get("href")
        summary = ""
        stories.append({
            "source": source_name,
            "headline": headline,
            "summary": summary,
            "link": link
        })
    return stories


def extract_kirkland_news(html, source_name):
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.find_all("div", class_="news-item"):
        a = item.find("a")
        if not a or not a.get("href"):
            continue
        headline = a.get_text(strip=True)
        link = "https://www.kirkland.com" + a.get("href")
        summary = ""
        p = item.find("p")
        if p:
            summary = p.get_text(strip=True)
        stories.append({
            "source": source_name,
            "headline": headline,
            "summary": summary,
            "link": link
        })
    return stories


def extract_hl_insights(html, source_name):
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.find_all("div", class_="insights-item"):
        a = item.find("a")
        if not a or not a.get("href"):
            continue
        headline = a.get_text(strip=True)
        link = "https://hl.com" + a.get("href")
        summary = ""
        p = item.find("p")
        if p:
            summary = p.get_text(strip=True)
        stories.append({
            "source": source_name,
            "headline": headline,
            "summary": summary,
            "link": link
        })
    return stories


def extract_stories_from_source(src, html):
    if src["name"] == "Reuters Bankruptcy":
        return extract_reuters_bankruptcy(html, src["name"])
    if src["name"] == "OFAC Press Releases":
        return extract_ofac_press(html, src["name"])
    if src["name"] == "Kirkland & Ellis News":
        return extract_kirkland_news(html, src["name"])
    if src["name"] == "Houlihan Lokey Insights":
        return extract_hl_insights(html, src["name"])
    return []


def is_relevant(text):
    t = text.lower()
    keywords = (
        DISTRESSED_KEYWORDS
        + NAMED_ISSUER_KEYWORDS
        + LAW_FIRM_KEYWORDS
        + ADVISER_COMPETITOR_KEYWORDS
        + CLEARING_SANCTIONS_KEYWORDS
    )
    return any(k in t for k in keywords)


def classify_service_line(text, source):
    t = text.lower()
    if any(k in t for k in ["consent solicitation", "bondholder meeting", "scheme", "chapter 11"]):
        return "Consent solicitations / liability management / court processes"
    if any(k in t for k in ["trustee", "notes", "bond", "rmbs", "mortgage-backed", "term loan"]):
        return "Bond trusteeship / loan & security agency"
    if any(k in t for k in ["escrow", "holdback", "earn-out"]):
        return "Escrow / M&A holdbacks"
    if any(k in t for k in ["sanctions", "ofac"]):
        return "Sanctions / cross-border risk"
    if source.startswith("Kirkland") or any(k in t for k in LAW_FIRM_KEYWORDS):
        return "Law-firm restructuring mandate / relationship opportunity"
    if source.startswith("Houlihan") or any(k in t for k in ADVISER_COMPETITOR_KEYWORDS):
        return "Financial adviser / competitor activity"
    return "General DCM / relationship"


def build_briefing(stories):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"i2 Capital Markets – Distressed & Restructuring Briefing — {now}",
        "=" * 80,
        ""
    ]
    if not stories:
        lines.append("No clearly relevant distressed/restructuring stories in the last cycle.")
        return "\n".join(lines)

    for s in stories:
        text = (s["headline"] + " " + s.get("summary", ""))
        why = classify_service_line(text, s["source"])
        lines.append(f"{s['headline']}")
        if s.get("summary"):
            lines.append(f"Summary: {s['summary']}")
        lines.append(f"Why it matters to i2: {why} (service line / relationship)")
        lines.append(f"Source: {s['source']}")
        lines.append(f"Link: {s['link']}")
        lines.append("")
    return "\n".join(lines)


def main():
    relevant = []
    for src in SOURCES:
        html = fetch_html(src["url"])
        stories = extract_stories_from_source(src, html)
        for st in stories:
            text = (st["headline"] + " " + st.get("summary", ""))
            if is_relevant(text):
                relevant.append(st)

    briefing = build_briefing(relevant)
    out_path = "/home/s68uk/i2_dcm_automation/briefing.txt"
    with open(out_path, "w") as f:
        f.write(briefing)
    print("Briefing generated:", out_path)


if __name__ == "__main__":
    main()
