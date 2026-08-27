#!/usr/bin/env python3
"""
PACKAGE: 3
Last updated: 27 Aug 2026 — "Why it matters to i2" is now a short
category-style tag (e.g. "Competitor Activity") instead of a full
sentence repeating the entity/category name on every line.

i2 DCM Briefing Automation — updated 27 Aug 2026

Changes from the previous version:
  1. Relevance/classification logic replaced with the tested rule-based
     scorer (co-occurrence gating on "financial advisor"/"recovery plan",
     self-referential exclusion for firm/adviser/competitor's own affairs,
     word-stem matching for administration/administrator, named-situation
     dedupe) — validated against real sample data before going into this
     script. See DECISIONS.md for the specific false positives this fixes.
  2. Cross-source dedupe added (e.g. the same restructuring reported by
     both a law firm and a financial adviser is now merged, not duplicated).
  3. Actual email delivery added via SMTP — the previous version only
     wrote briefing.txt locally despite the README describing email
     delivery; this was never implemented until now.
  4. Per-source failures are now logged explicitly (0 stories from a
     source triggers a warning line in the log) instead of failing silently.
  5. README's claim of "Claude-generated" summarisation has been corrected —
     there is no LLM call in this script. Classification here is entirely
     rule-based. See README.md for where the Cowork-based judgement layer
     is intended to plug in downstream of this script's digest output.

NOTE ON SELECTORS: the per-source HTML extractors below (extract_reuters_*,
extract_kirkland_*, etc.) use CSS selectors that have NOT been verified
against the live site markup — they were carried over from the original
script. Check the log for "0 stories from <source>" warnings; that is the
signal a selector needs re-checking against the live page.
"""
import os
import re
import json
import time
import signal
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
LOG_PATH = BASE_DIR / "briefing.log"
OUT_PATH = BASE_DIR / "briefing.txt"
KEYWORDS_PATH = BASE_DIR / "keywords.json"
LOCK_PATH = BASE_DIR / "briefing.lock"
OVERALL_TIMEOUT_SECONDS = 120  # hard ceiling for the whole run

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("i2_dcm_briefing")


# ---------------------------------------------------------------------------
# Keyword configuration (mirrors config/keywords.yaml from the pipeline test)
# Kept inline as JSON here to avoid adding a yaml dependency to this script;
# keywords.json should be updated whenever keywords.yaml changes.
# ---------------------------------------------------------------------------
DEFAULT_KEYWORDS = {
    "core": [
        "default", "missed coupon", "covenant breach", "restructuring",
        "chapter 11", "scheme of arrangement", "debt exchange",
        "tender offer", "consent solicitation", "liability management",
        "bondholder", "noteholder", "administration", "administrator appointed",
    ],
    "instruments_structures": [
        "rmbs", "mortgage-backed", "senior notes", "secured notes",
        "term loan", "bridge facility",
    ],
    "sanctions_cross_border": ["sanctions", "ofac", "sdn list", "blocked property"],
    "extended": [
        "restructuring advisor", "creditor committee", "liability management exercise",
        "exchange offer", "waiver", "amend and extend", "new money", "haircut",
        "distressed", "recovery plan", "insolvency", "liquidation",
        "scheme meeting", "trustee appointed", "agency appointment",
    ],
    "co_occurrence_only": ["financial advisor", "recovery plan"],
    "ma_escrow_terms": ["escrow", "holdback", "earn-out"],
    "ma_escrow_entities": [
        "lloyds", "hsbc", "bny", "citibank", "ebury partners", "corpay", "alpha fx"
    ],
    "named_situations": [
        "air baltic", "nord stream", "schleich", "sound energy", "paratus amc",
        "goldman sachs", "morgan stanley", "blackrock", "intesa sanpaolo",
        "thames water", "southern water", "fantasia holdings", "kwg group",
        "lycra company", "wallbox", "segula", "new fortress energy",
        "grupo antolin", "lithium americas", "first brands",
    ],
    "law_firms": [
        "milbank", "kirkland & ellis", "linklaters", "a&o shearman",
        "akin gump", "latham & watkins", "gibson dunn",
    ],
    "advisers_competitors_systems": [
        "houlihan lokey", "pjt partners", "fti consulting", "kroll", "glas",
        "euroclear", "clearstream", "dtc",
    ],
}

INTERNAL_FIRM_MARKERS = [
    "partner departure", "partner departures", "headcount reduction",
    "headcount reductions", "cost-cutting", "cost cutting", "post-merger",
    "office closure", "layoffs", "job cuts", "redundancies",
    "firm-wide restructuring", "law firm restructuring",
    "hires", "hired", "joins its", "joining its", "joins the firm",
    "announced the hire", "addition of partner", "adds partner",
    "welcomes partner", "new partner", "joins as partner", "partner hire",
    "hires partner", "lateral hire", "lateral partner",
    "stake in", "takes a stake", "took a stake", "acquired a stake",
    "majority stake", "minority stake", "ownership restructuring",
    "ownership of the firm",
]
ADMINISTRATION_STEM_RE = re.compile(r"administrat\w*")

CLEARING_SYSTEMS = {"euroclear", "clearstream", "dtc"}
COMPETITORS = {"kroll", "glas"}


def load_keywords():
    if KEYWORDS_PATH.exists():
        with open(KEYWORDS_PATH) as f:
            return json.load(f)
    return DEFAULT_KEYWORDS


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
SOURCES = [
    {"name": "OFAC Recent Actions", "url": "https://ofac.treasury.gov/recent-actions", "type": "sanctions"},
    {"name": "Kirkland & Ellis News", "url": "https://www.kirkland.com/insights", "type": "law_firm", "known_firm": "kirkland & ellis"},
    {"name": "Akin Gump Press Releases", "url": "https://www.akingump.com/en/insights?nt=1063235", "type": "law_firm", "known_firm": "akin gump"},
    {"name": "FTI Consulting Insights", "url": "https://www.fticonsulting.com/insights", "type": "adviser", "known_firm": "fti consulting"},
    {"name": "FT Alphaville", "url": "https://ftav.substack.com/archive", "type": "news"},
    {"name": "Milbank News", "url": "https://www.milbank.com/en/news-at-milbank.html", "type": "law_firm", "known_firm": "milbank"},
    {"name": "GLAS News", "url": "https://glas.agency/news/", "type": "adviser", "known_firm": "glas"},
]

# Tried and confirmed BLOCKED 27 Aug 2026 — all use enterprise JS-driven
# search/grid widgets whose article data isn't present in the initial HTML:
#   - Linklaters, A&O Shearman, Kroll (Next.js client-side data fetch)
#   - Latham & Watkins (Coveo enterprise search widget)
#   - Gibson Dunn (WP Grid Builder plugin, AJAX-loaded grid)
# PJT Partners was checked but excluded for a different reason: their
# actual press-releases feed is pure investor-relations content (earnings,
# CFO transitions, buybacks) — no client-facing restructuring mandates are
# published there at all, so it wouldn't produce real Option A/D signal
# even if it were fetchable.

# Known-blocked sources — kept here as a record, NOT in the active SOURCES
# list above, so the pipeline doesn't waste a request/timeout on them every
# run. Both need a browser-based fetch (not plain requests+BeautifulSoup)
# to work at all:
#   - Houlihan Lokey (hl.com/insights): behind Incapsula bot-protection,
#     returns a JS challenge page regardless of URL/selector.
#   - Reuters (reuters.com/business/bankruptcy/): 403s non-browser traffic;
#     no official RSS feed exists for this section either.
BLOCKED_SOURCES_NEEDING_BROWSER_FETCH = [
    {"name": "Houlihan Lokey Insights", "url": "https://hl.com/insights/"},
    {"name": "Reuters Bankruptcy", "url": "https://www.reuters.com/business/bankruptcy/"},
]


def fetch_html(url, timeout=15, retries=2, backoff_seconds=3):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "i2CapitalMarkets-BriefingBot/1.0"})
            r.raise_for_status()
            return r.text
        except Exception as e:
            log.warning("Fetch attempt %d/%d failed for %s: %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
    return None


def extract_ofac_recent_actions(html, source_name):
    """ofac.treasury.gov/recent-actions — real structure confirmed 27 Aug 2026:
    <div class="search-result views-row"><div><div class="font-sans-lg...">
    <a href="/recent-actions/YYYYMMDD">Headline text</a></div></div>...</div>
    No summary text available on the listing page itself.
    """
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("div", class_="search-result"):
        a = row.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        stories.append({
            "source": source_name,
            "headline": a.get_text(strip=True),
            "summary": "",
            "link": "https://ofac.treasury.gov" + href if href.startswith("/") else href,
        })
    return stories


def extract_kirkland_news(html, source_name):
    """kirkland.com/insights — real structure confirmed 27 Aug 2026:
    <a class="insight-card" href="/news/...">
      <span class="insight-card__meta">
        <time class="insight-card__date">...</time>
        <span class="insight-card__category">Press Release</span>
      </span>
      <span class="insight-card__title">Headline text</span>
    </a>
    No summary text available on the listing page itself.
    """
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", class_="insight-card"):
        href = a.get("href")
        title_el = a.find("span", class_="insight-card__title")
        if not href or not title_el:
            continue
        stories.append({
            "source": source_name,
            "headline": title_el.get_text(strip=True),
            "summary": "",
            "link": "https://www.kirkland.com" + href if href.startswith("/") else href,
        })
    return stories


def extract_akin_gump_news(html, source_name):
    """akingump.com/en/insights?nt=1063235 (press releases filter) — real
    structure confirmed 27 Aug 2026. Uses class*= substring matching rather
    than exact hashed class names (e.g. "styles__insightCardBody--_f2216f7")
    since these hashes are CSS-module-generated and may change on Akin's
    next site rebuild — matching on the stable substring is more durable.
    No summary text available on the listing page itself.
    """
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select('div[class*="insightCardBody"]'):
        heading_a = card.select_one('p[class*="heading"] a')
        if not heading_a or not heading_a.get("href"):
            continue
        date_el = card.select_one('p[class*="date"]')
        href = heading_a["href"]
        stories.append({
            "source": source_name,
            "headline": heading_a.get_text(strip=True),
            "summary": "",
            "link": href if href.startswith("http") else "https://www.akingump.com" + href,
            "date_text": date_el.get_text(strip=True) if date_el else "",
        })
    return stories


def extract_fti_insights(html, source_name):
    """fticonsulting.com/insights — real structure confirmed 27 Aug 2026:
    <a class="insights-card ..." href="/insights/...">
      <span class="insights-card__contents">
        <span class="insights-card__contents-header">Headline</span>
        <p class="insights-card__contents-text">Date — summary text</p>
      </span>
    </a>
    NOTE: this is FTI's general insights feed across ALL practice areas
    (health tech, infrastructure, AI, labor, etc.), not restructuring-
    filtered — expect mostly non-distress noise day to day, same as
    Kirkland's /insights page. The scorer's keyword/co-occurrence logic is
    what does the actual filtering here, same as any broad source.
    """
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.find_all("a", class_="insights-card"):
        header = card.find(class_="insights-card__contents-header")
        text_p = card.find(class_="insights-card__contents-text")
        href = card.get("href")
        if not header or not href:
            continue
        summary = ""
        if text_p:
            raw = text_p.get_text(strip=True)
            # Format is "August 26, 2026 — summary text"; strip the date prefix
            summary = raw.split("—", 1)[-1].strip() if "—" in raw else raw
        stories.append({
            "source": source_name,
            "headline": header.get_text(strip=True),
            "summary": summary,
            "link": href if href.startswith("http") else "https://www.fticonsulting.com" + href,
        })
    return stories


def extract_ftav_archive(html, source_name):
    """ftav.substack.com/archive — real structure confirmed 27 Aug 2026:
    <a data-testid="post-preview-title" href="...">Witty title</a>
    followed by a sibling <a> with the actual descriptive subtitle, e.g.
    "Also: container ships, money market funds, First Brands, ..."

    IMPORTANT: FT Alphaville's post titles are deliberately witty/non-
    descriptive ("Torque is cheap", "Money has ruined markets") — almost
    never keyword-matchable on their own. The SUBTITLE line is where the
    actual topic list lives and is what scoring should run against. This
    is also a weekly roundup format (one post ~ 8 topics), so expect a
    genuinely lower hit rate than a deal-specific press release even when
    working correctly — that's the nature of the source, not a bug.
    """
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for title_a in soup.find_all("a", attrs={"data-testid": "post-preview-title"}):
        href = title_a.get("href")
        if not href:
            continue
        card = title_a.find_parent(
            "div", class_=lambda c: c and "container-H2dyKk" in c
        )
        subtitle = ""
        if card:
            all_a = card.find_all("a")
            if len(all_a) > 1:
                subtitle = all_a[1].get_text(strip=True)
        stories.append({
            "source": source_name,
            "headline": title_a.get_text(strip=True),
            "summary": subtitle,
            "link": href,
        })
    return stories


def extract_milbank_news(html, source_name):
    """milbank.com/en/news-at-milbank.html — real structure confirmed
    27 Aug 2026: <li class="item-list__item">...<a class="item-list__text"
    href="...">Headline</a>...<span class="type__news-date">MM/DD/YYYY</span>
    No summary text on the listing page.
    """
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.find_all("li", class_="item-list__item"):
        a = item.find("a", class_="item-list__text")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        stories.append({
            "source": source_name,
            "headline": a.get_text(strip=True),
            "summary": "",
            "link": href if href.startswith("http") else "https://www.milbank.com" + href,
        })
    return stories


def extract_glas_news(html, source_name):
    """glas.agency/news/ — real structure confirmed 27 Aug 2026: a
    WPBakery grid where each item appears as TWO <a class="vc_gitem-link">
    tags with identical href/title (one wrapping the thumbnail image, one
    the text) — deduped here by URL. Headline comes from the link's
    title attribute, not its (empty) text content. No summary available.
    """
    stories = []
    if not html:
        return stories
    soup = BeautifulSoup(html, "html.parser")
    seen_links = set()
    for a in soup.find_all("a", class_="vc_gitem-link"):
        href = a.get("href")
        title = a.get("title")
        if not href or not title or href in seen_links:
            continue
        seen_links.add(href)
        stories.append({
            "source": source_name,
            "headline": title.strip(),
            "summary": "",
            "link": href,
        })
    return stories


EXTRACTORS = {
    "OFAC Recent Actions": extract_ofac_recent_actions,
    "Kirkland & Ellis News": extract_kirkland_news,
    "Akin Gump Press Releases": extract_akin_gump_news,
    "FTI Consulting Insights": extract_fti_insights,
    "FT Alphaville": extract_ftav_archive,
    "Milbank News": extract_milbank_news,
    "GLAS News": extract_glas_news,
}


def extract_stories_from_source(src, html):
    fn = EXTRACTORS.get(src["name"])
    stories = fn(html, src["name"]) if fn else []
    known_firm = src.get("known_firm")
    if known_firm:
        for s in stories:
            s["known_firm"] = known_firm
    return stories


# ---------------------------------------------------------------------------
# Scoring (tested logic, ported from the pipeline validation)
# ---------------------------------------------------------------------------
def _find_hits(text_lower, terms):
    return [t for t in terms if t.lower() in text_lower]


def score_story(story, kw):
    text_lower = (story["headline"] + " " + story.get("summary", "")).lower()

    core_hits = _find_hits(text_lower, kw["core"])
    instrument_hits = _find_hits(text_lower, kw["instruments_structures"])
    sanctions_hits = _find_hits(text_lower, kw["sanctions_cross_border"])
    extended_terms = [t for t in kw["extended"] if t not in kw["co_occurrence_only"]]
    extended_hits = _find_hits(text_lower, extended_terms)
    co_occurrence_hits = _find_hits(text_lower, kw["co_occurrence_only"])

    if ADMINISTRATION_STEM_RE.search(text_lower) and "administration" not in extended_hits \
            and "administration" not in core_hits:
        extended_hits.append("administration")

    named_hits = _find_hits(text_lower, kw["named_situations"])
    firm_hits = _find_hits(text_lower, kw["law_firms"])
    adviser_hits = _find_hits(text_lower, kw["advisers_competitors_systems"])
    escrow_term_hits = _find_hits(text_lower, kw.get("ma_escrow_terms", []))
    escrow_entity_hits = _find_hits(text_lower, kw.get("ma_escrow_entities", []))
    # Escrow entities (Lloyds, HSBC, BNY, Citibank, etc.) are too generic to
    # trigger alone — these banks appear in unrelated news constantly. Only
    # count them as an M&A escrow signal when an escrow/holdback/earn-out
    # term is also present in the same item.
    if not escrow_term_hits:
        escrow_entity_hits = []

    # Attribute the known source firm even if the text itself uses a
    # shortened/rebranded name that wouldn't otherwise match (e.g. Akin
    # Gump's own press releases now say "Akin", not "Akin Gump" — found via
    # live data 27 Aug 2026). We know which firm a single-firm source
    # belongs to regardless of what the text says.
    known_firm = story.get("known_firm")
    if known_firm:
        if known_firm in kw["law_firms"] and known_firm not in firm_hits:
            firm_hits.append(known_firm)
        elif known_firm in kw["advisers_competitors_systems"] and known_firm not in adviser_hits:
            adviser_hits.append(known_firm)

    any_core_signal = bool(core_hits or instrument_hits or extended_hits or sanctions_hits)
    if not any_core_signal:
        co_occurrence_hits = []

    all_distress_hits = core_hits + instrument_hits + sanctions_hits + extended_hits + co_occurrence_hits

    matched_clearing = [a for a in adviser_hits if a.lower() in CLEARING_SYSTEMS]
    matched_competitor = [a for a in adviser_hits if a.lower() in COMPETITORS]
    matched_other_adviser = [a for a in adviser_hits if a.lower() not in CLEARING_SYSTEMS and a.lower() not in COMPETITORS]

    any_named_entity = bool(firm_hits or matched_other_adviser or matched_competitor or matched_clearing)
    internal_marker_hit = any(m in text_lower for m in INTERNAL_FIRM_MARKERS)
    is_self_referential = any_named_entity and internal_marker_hit and not named_hits

    # --- Category assignment (single primary category per item, priority
    # order below) + the "why it matters to i2" line the report shows. ---
    category, why_it_matters = None, None

    if is_self_referential:
        category = "firm_intel"
        why_it_matters = "Firm/Adviser/Competitor Intel"
    elif named_hits:
        category = "named_deals"
        why_it_matters = "Named Situation"
    elif escrow_term_hits:
        category = "ma_escrow"
        why_it_matters = "M&A Escrow"
    elif matched_competitor and any_core_signal:
        category = "competitor_moves"
        why_it_matters = "Competitor Activity"
    elif firm_hits and any_core_signal:
        category = "law_firm_news"
        why_it_matters = "Law-Firm Mandate"
    elif matched_other_adviser and any_core_signal:
        category = "adviser_activity"
        why_it_matters = "Adviser Mandate"
    elif matched_clearing:
        category = "clearing_systems"
        why_it_matters = "Clearing System Change"
    elif sanctions_hits:
        category = "sanctions"
        why_it_matters = "Sanctions / Cross-Border Risk"
    elif any_core_signal:
        category = "distress_general"
        why_it_matters = "Distressed-Debt Situation"
    elif firm_hits or matched_other_adviser or matched_competitor:
        category = "excluded"
        why_it_matters = None
    else:
        category = "excluded"
        why_it_matters = None

    return {
        **story,
        "category": category,
        "why_it_matters": why_it_matters,
        "matched_named_situations": named_hits,
        "matched_law_firms": firm_hits,
        "matched_advisers": matched_other_adviser,
        "matched_competitors": matched_competitor,
        "matched_clearing": matched_clearing,
        "matched_escrow_entities": escrow_entity_hits,
        "matched_distress_terms": all_distress_hits,
    }


def dedupe(scored):
    merged, used = [], set()
    for i, item in enumerate(scored):
        key = item["link"]
        if key in used:
            continue
        if not item["matched_named_situations"]:
            merged.append(item)
            used.add(key)
            continue
        group = [item]
        used.add(key)
        for other in scored:
            if other["link"] in used or not other["matched_named_situations"]:
                continue
            shared = set(e.lower() for e in item["matched_named_situations"]) & \
                     set(e.lower() for e in other["matched_named_situations"])
            if shared:
                group.append(other)
                used.add(other["link"])
        if len(group) == 1:
            merged.append(item)
        else:
            base = dict(group[0])
            base["matched_law_firms"] = sorted(set().union(*[set(g["matched_law_firms"]) for g in group]))
            base["matched_advisers"] = sorted(set().union(*[set(g["matched_advisers"]) for g in group]))
            base["all_sources"] = [g["source"] for g in group]
            merged.append(base)
    return merged


# ---------------------------------------------------------------------------
# Digest rendering + email
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Digest rendering + email
# ---------------------------------------------------------------------------

# Fixed report order, matching the categories in the original requirement.
# (category_key, printed heading)
REPORT_CATEGORIES = [
    ("distress_general", "Distressed-Debt & Restructuring Situations"),
    ("named_deals", "Named Deals & Situations i2 Already Touches"),
    ("law_firm_news", "Law Firm Restructuring Practice News"),
    ("adviser_activity", "Financial Adviser Activity"),
    ("competitor_moves", "Competitor Moves"),
    ("clearing_systems", "Clearing System Changes"),
    ("ma_escrow", "M&A Holdback / Escrow Activity"),
    ("sanctions", "Sanctions / OFAC"),
]
# Shown after the main categories, not part of the numbered requirement list,
# but kept since it's genuinely useful and was in earlier versions of this
# report — personnel/ownership news about firms/advisers/competitors
# themselves, not a client situation.
FIRM_INTEL_HEADING = "Firm / Adviser / Competitor Intel"


def build_briefing(scored_deduped):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    by_category = {key: [] for key, _ in REPORT_CATEGORIES}
    firm_intel = []
    for s in scored_deduped:
        cat = s.get("category")
        if cat in by_category:
            by_category[cat].append(s)
        elif cat == "firm_intel":
            firm_intel.append(s)
        # "excluded" items are dropped entirely — true noise, not printed.

    lines = [f"i2 Capital Markets — Distressed & Restructuring Briefing — {now}", "=" * 80]

    for key, heading in REPORT_CATEGORIES:
        items = by_category[key]
        lines.append(f"\n{heading} ({len(items)} items)")
        lines.append("-" * 80)
        if not items:
            lines.append("Nothing relevant in this cycle.")
            continue
        for s in items:
            lines.append(f"\n{s['headline']}")
            if s.get("summary"):
                lines.append(f"Summary: {s['summary']}")
            if s.get("why_it_matters"):
                lines.append(f"Why it matters to i2: {s['why_it_matters']}")
            firms = s.get("matched_law_firms", []) + s.get("matched_advisers", [])
            if firms and key not in ("law_firm_news", "adviser_activity"):
                # avoid repeating the same firm name the category already names
                lines.append(f"Adviser(s): {', '.join(firms)}")
            if s.get("matched_competitors") and key != "competitor_moves":
                lines.append(f"** Also competitor activity: {', '.join(s['matched_competitors'])} **")
            lines.append(f"Source: {s['source']}  |  Link: {s['link']}")

    lines.append(f"\n{FIRM_INTEL_HEADING} ({len(firm_intel)} items)")
    lines.append("-" * 80)
    if not firm_intel:
        lines.append("Nothing relevant in this cycle.")
    for s in firm_intel:
        lines.append(f"- {s['headline']} ({s['source']})")

    return "\n".join(lines)


def send_email(subject, body):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    sender = os.environ.get("BRIEFING_SENDER", user)
    recipient = os.environ.get("BRIEFING_RECIPIENT", user)

    if not all([host, user, password]):
        log.warning("SMTP env vars not fully set — skipping email, briefing.txt still written locally")
        return False

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(sender, [recipient], msg.as_string())
        log.info("Briefing emailed to %s", recipient)
        return True
    except Exception as e:
        log.error("Email send failed: %s", e)
        return False


def load_env_file():
    """Read .env directly rather than relying on the shell to have exported
    it — cron does NOT source .bashrc, so the export-then-source approach
    in earlier setup instructions silently fails for scheduled runs (it only
    works for manual, interactive runs). This makes the script self-sufficient
    regardless of how it's invoked."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()


class TimeoutError_(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError_(f"Run exceeded {OVERALL_TIMEOUT_SECONDS}s overall timeout")


def acquire_lock():
    """Refuse to start a second run if one is already in progress —
    prevents overlapping cron executions if a prior run is stuck."""
    if LOCK_PATH.exists():
        # Stale lock check: if it's older than the overall timeout by a
        # comfortable margin, assume the previous run died without cleaning
        # up and proceed anyway rather than blocking forever.
        age = datetime.now().timestamp() - LOCK_PATH.stat().st_mtime
        if age < OVERALL_TIMEOUT_SECONDS * 2:
            log.error("Lock file present and recent (%.0fs old) — another run may be in progress, aborting", age)
            print("Aborting: briefing.lock present and recent. Another run may be active.")
            return False
        log.warning("Stale lock file (%.0fs old) — removing and proceeding", age)
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception as e:
        log.warning("Could not remove lock file: %s", e)


def main():
    if not acquire_lock():
        return

    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(OVERALL_TIMEOUT_SECONDS)

    try:
        kw = load_keywords()
        all_stories = []
        for src in SOURCES:
            html = fetch_html(src["url"])
            stories = extract_stories_from_source(src, html)
            log.info("%s: %d stories fetched", src["name"], len(stories))
            if len(stories) == 0:
                log.warning("0 stories from %s — check selector against live page", src["name"])
            all_stories.extend(stories)

        scored = [score_story(s, kw) for s in all_stories]
        deduped = dedupe(scored)

        briefing = build_briefing(deduped)
        OUT_PATH.write_text(briefing)
        log.info("Briefing written to %s", OUT_PATH)
        print("Briefing generated:", OUT_PATH)

        subject = f"i2 DCM Briefing — {datetime.now().strftime('%Y-%m-%d')}"
        sent = send_email(subject, briefing)
        print("Email sent:" if sent else "Email NOT sent (see briefing.log):", sent)

    except TimeoutError_ as e:
        log.error("ABORTED: %s", e)
        print(f"Aborted: {e}")
    finally:
        signal.alarm(0)  # cancel the alarm if we finished normally
        release_lock()


if __name__ == "__main__":
    main()
