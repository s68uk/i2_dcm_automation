#!/usr/bin/env python3
"""
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
    "named_situations": [
        "air baltic", "nord stream", "schleich", "sound energy", "paratus amc",
        "goldman sachs", "morgan stanley", "blackrock", "intesa sanpaolo",
        "thames water", "southern water", "fantasia holdings", "kwg group",
        "lycra company", "wallbox", "segula", "new fortress energy",
        "grupo antolin", "lithium americas",
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
    "announced the hire",
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
    {"name": "Kirkland & Ellis News", "url": "https://www.kirkland.com/insights", "type": "law_firm"},
]

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


EXTRACTORS = {
    "OFAC Recent Actions": extract_ofac_recent_actions,
    "Kirkland & Ellis News": extract_kirkland_news,
}


def extract_stories_from_source(src, html):
    fn = EXTRACTORS.get(src["name"])
    return fn(html, src["name"]) if fn else []


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

    bucket, reasons = None, []
    if is_self_referential:
        bucket = "firm_intel"
        reasons.append("firm/adviser/competitor's own affairs — competitive intel, not client distress")
    elif matched_clearing and not any_core_signal:
        bucket = "clearing_ops_watch"
        reasons.append("clearing/settlement infrastructure update")
    elif matched_competitor and any_core_signal:
        bucket = "option_a_and_competitor_activity"
        reasons.append("competitor acting on a genuine distress situation")
    elif any_core_signal or named_hits:
        bucket = "option_a_and_option_d" if (firm_hits or matched_other_adviser) else "option_a"
        reasons.append("distress signal matched")
    elif firm_hits or matched_other_adviser or matched_competitor:
        bucket = "excluded"
        reasons.append("firm/adviser named but no distress signal co-occurring")
    else:
        bucket = "excluded"
        reasons.append("no relevant signal matched")

    return {
        **story,
        "bucket": bucket,
        "matched_named_situations": named_hits,
        "matched_law_firms": firm_hits,
        "matched_advisers": matched_other_adviser,
        "matched_competitors": matched_competitor,
        "matched_clearing": matched_clearing,
        "matched_distress_terms": all_distress_hits,
        "reasons": reasons,
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
def build_briefing(scored_deduped):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    option_a = [s for s in scored_deduped if s["bucket"] in
                ("option_a", "option_a_and_option_d", "option_a_and_competitor_activity")]
    ops_watch = [s for s in scored_deduped if s["bucket"] == "clearing_ops_watch"]
    firm_intel = [s for s in scored_deduped if s["bucket"] == "firm_intel"]

    lines = [f"i2 Capital Markets — Distressed & Restructuring Briefing — {now}", "=" * 80, ""]

    lines.append(f"OPTION A — Distressed-debt & restructuring ({len(option_a)} items)")
    lines.append("-" * 80)
    if not option_a:
        lines.append("No clearly relevant distressed/restructuring stories in this cycle.")
    for s in option_a:
        lines.append(f"\n{s['headline']}")
        if s.get("summary"):
            lines.append(f"Summary: {s['summary']}")
        firms = s.get("matched_law_firms", []) + s.get("matched_advisers", [])
        if firms:
            lines.append(f"Adviser(s): {', '.join(firms)}")
        if s.get("matched_competitors"):
            lines.append(f"** Competitor activity: {', '.join(s['matched_competitors'])} **")
        lines.append(f"Source: {s['source']}  |  Link: {s['link']}")

    if firm_intel:
        lines.append(f"\n\nFIRM/ADVISER/COMPETITOR INTEL ({len(firm_intel)} items)")
        lines.append("-" * 80)
        for s in firm_intel:
            lines.append(f"- {s['headline']} ({s['source']})")

    if ops_watch:
        lines.append(f"\n\nCLEARING/OPS WATCH ({len(ops_watch)} items)")
        lines.append("-" * 80)
        for s in ops_watch:
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
