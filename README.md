# i2 DCM Briefing Automation

**PACKAGE: 1**
**Last updated: 27 Aug 2026** — removed remaining "Option A" wording from the docs (the briefing report itself already dropped this label; see `i2_dcm_briefing.py` Package 1).

Daily distressed-debt capital markets briefing generator for **i2 Capital Markets**, running on a **Raspberry Pi 400**.

**Corrected 27 Aug 2026:** the previous version of this README described a Claude-API-powered summarisation step and SMTP email delivery. Neither was actually implemented in the code at the time. This version fixes that gap: email delivery is now real, and the README below describes what the script actually does — rule-based classification, no LLM call.

## What this actually does

1. Fetches HTML from a small set of public sources (currently: OFAC Recent Actions, Kirkland & Ellis News — see "Source status" below for why the original four-source list is currently two).
2. Extracts headlines/links using source-specific CSS selectors, verified against live pages on 27 Aug 2026.
3. Scores each item against `keywords.json` — core distress terms, instruments, sanctions, an extended term set, named situations, law firms, and advisers/competitors/clearing systems.
4. Applies a few disambiguation rules found through testing (see below), dedupes cross-source reports of the same situation, and buckets everything into the distressed-debt briefing, Option D (adviser tracker), competitor activity, clearing/ops watch, or firm-intel.
5. Writes `briefing.txt` locally and emails it via SMTP.

**No LLM is called anywhere in this script.** Classification is entirely rule-based. A Claude-based judgement layer (via a Claude Cowork scheduled task) is planned as a downstream step that would read this script's output — not a change to this script itself, since no Anthropic API key is available for headless use.

## Source status (as of 27 Aug 2026)

| Source | Status |
|---|---|
| OFAC Recent Actions (`ofac.treasury.gov/recent-actions`) | Working |
| Kirkland & Ellis News (`kirkland.com/insights`) | Working — note: this page only shows ~7-9 most recent items across *all* categories (press releases, awards, conferences), so a high-volume day could see a genuine story scroll off before the next run. Not yet solved. |
| Akin Gump Press Releases (`akingump.com/en/insights?nt=1063235`) | Working — note: Akin Gump rebranded to just "Akin" and their own press releases reflect this, so text-based firm matching alone would miss them; the source config now carries a `known_firm` attribution instead of relying on keyword matching for this. |
| FTI Consulting Insights (`fticonsulting.com/insights`) | Working — note: this is FTI's *general* insights feed across all practice areas (health tech, infrastructure, AI, labor, etc.), not restructuring-filtered, so expect a lot of non-distress noise most days — the scorer's keyword logic is what filters it, same as Kirkland's page. |
| FT Alphaville (`ftav.substack.com/archive`) | Working — note: FTAV's post *titles* are deliberately witty/non-descriptive ("Torque is cheap") and almost never keyword-matchable; the actual topic list lives in the *subtitle* ("Also: container ships, money market funds, First Brands..."), which is what scoring runs against. This is also a weekly roundup format (~8 topics per post), so expect a genuinely lower hit rate than a deal-specific press release even when working correctly. |
| Milbank News (`milbank.com/en/news-at-milbank.html`) | Working — broad feed like Kirkland's, expect noise most days. |
| GLAS News (`glas.agency/news/`) | Working — **highest-signal source found so far**: GLAS publishes almost exclusively genuine agent/trustee restructuring mandates (Fantasia Holdings, Cornaglia Group, The LYCRA Company, Development Bank of Mongolia), unlike the broader feeds. Note: each item appears as two duplicate `<a>` tags in the raw HTML (image + text link to the same URL) — the extractor dedupes by URL. |
| Latham & Watkins (`lw.com/en/news`) | Blocked — Coveo enterprise search widget; article data is fetched client-side, not in the initial HTML. |
| Gibson Dunn (`gibsondunn.com/insights/insights-archive/`) | Blocked — WP Grid Builder plugin loads the article grid via AJAX; same "no data in static HTML" problem. |
| PJT Partners | **Excluded, different reason**: their actual press-releases feed (`ir.pjtpartners.com`) is pure investor-relations content (earnings, CFO transitions, buybacks) — no client-facing restructuring mandates are published there at all. Not a fetch problem; there's genuinely no distress/adviser signal to find on this page. |
| Houlihan Lokey Insights | Blocked — Incapsula bot-protection returns a JS challenge page regardless of URL/selector. Needs a browser-based fetch, not `requests`. |
| Reuters Bankruptcy | Blocked — 403s non-browser traffic; no official RSS feed exists for this section. Same fix needed as Houlihan Lokey. |
| Linklaters (`news-and-deals`) | Blocked — Next.js app; article list is fetched client-side after page load, not present in the initial HTML (confirmed via `__NEXT_DATA__` inspection). Needs a browser-based fetch. |
| A&O Shearman (`newsroom`) | Blocked — same client-side-fetch pattern as Linklaters. |
| Kroll (`insights`) | Blocked — same client-side-fetch pattern as Linklaters. |

- **Linklaters, A&O Shearman, Kroll**: Next.js apps, article data fetched client-side.
- **Latham & Watkins**: Coveo enterprise search widget.
- **Gibson Dunn**: WP Grid Builder plugin, AJAX-loaded grid.
- **Houlihan Lokey, Reuters**: active bot-blocking (Incapsula / 403s).

All five need a browser-based fetch (not `requests`) to ever work.

## Known classification edge cases (found via testing, handled in code)

- **A term describing the firm itself vs. a client**: e.g. a law firm's own internal restructuring/layoffs, or an adviser's own ownership change or personnel hire, is NOT a client distress signal. These are routed to a separate `firm_intel` bucket rather than the main distressed-debt briefing or silently dropped. This required a broad and growing list of personnel/ownership phrasing (found via live data: "addition of partner", "hires", "takes a stake", etc.) since firms phrase these announcements very differently from each other.
- **Rebranded/shortened firm names**: Akin Gump's own press releases now say "Akin," not "Akin Gump" — text matching alone would miss this and misattribute (or fail to flag as firm-intel) their own announcements. Fixed by attributing `known_firm` directly from the source config for single-firm sources, rather than relying solely on text matching.
- **Cross-source duplicates**: the same restructuring reported by both a law firm (as counsel) and a financial adviser (as adviser) is merged into one item, not published twice.
- **"financial advisor" and "recovery plan"** are too generic to trigger relevance on their own (they appear in almost any deal press release) — they only count if another core distress term also appears in the same item.
- **Sanctions items** count as relevant on their own (fixed 27 Aug 2026 — a bug had them silently excluded even when "sanctions"/"OFAC" matched).

## Reliability

- Every fetch has a timeout + 2 retries with backoff.
- Overall run is capped at 120 seconds (`signal.alarm`) — the run aborts rather than hanging indefinitely.
- A lock file (`briefing.lock`) prevents two overlapping cron runs; a stale lock (older than 2x the timeout) is auto-cleared rather than blocking forever.
- Per-source zero-result fetches are logged explicitly as warnings, not silently swallowed.

## Repository Structure

```
i2_dcm_automation/
├── i2_dcm_briefing.py     # Main automation script
├── keywords.json          # Keyword/entity configuration (edit this, not the .py, to tune matching)
├── requirements.txt       # requests, beautifulsoup4 — that's it
├── crontab.txt            # Reference cron schedule
├── .env.example           # Template — copy to .env on the Pi, never commit the real one
├── README.md
└── .gitignore             # Excludes .env, briefing.log, briefing.txt, briefing.lock, venv
```

## Installation (Raspberry Pi)

```bash
cd ~
git clone https://github.com/s68uk/i2_dcm_automation.git
cd i2_dcm_automation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Environment variables

```bash
cp .env.example .env
nano .env   # fill in real SMTP credentials
```

That's it — no `.bashrc`/`export` step needed. The script reads `.env` directly on startup (see `load_env_file()` in `i2_dcm_briefing.py`), so it works identically whether run manually or via cron. (An earlier version of this README suggested exporting the vars via `.bashrc` — that approach silently fails for cron specifically, since cron doesn't source `.bashrc` for non-interactive jobs. Fixed 27 Aug 2026.)

## Running manually

```bash
source ~/i2_dcm_automation/venv/bin/activate
python i2_dcm_briefing.py
```

Check `briefing.txt` for the digest and `briefing.log` for what happened (including any "0 stories from `<source>`" warnings, which mean a selector needs re-checking against the live page).

## Scheduling (cron)

```bash
crontab -e
```

Paste the line from `crontab.txt` — runs daily at 06:30 UK time.

## Updating from GitHub

```bash
cd ~/i2_dcm_automation
git pull
source venv/bin/activate
pip install -r requirements.txt   # only if requirements.txt changed
```

## Troubleshooting

```bash
cat ~/i2_dcm_automation/briefing.log      # check for fetch failures, 0-story warnings, email errors
systemctl status cron                      # confirm cron is running
ls ~/i2_dcm_automation/briefing.lock       # if this exists and is old, a prior run may have died uncleanly
```

## Security notes

- Never commit `.env`, `briefing.log`, or `briefing.txt` — `.gitignore` excludes all three.
- SMTP credentials should be app-passwords where possible.
- No LLM API key is required or used by this script.

## Roadmap

- Expand sources: Milbank, Linklaters, A&O Shearman, Akin Gump, Latham & Watkins, Gibson Dunn, PJT Partners, FTI Consulting, Kroll, GLAS, Euroclear/Clearstream/DTC, FT Companies/Debt, MarketWatch Credit Markets, Yahoo Finance Bankruptcy — each needs the same live-HTML verification process used for OFAC/Kirkland above before being added.
- Solve Reuters/Houlihan Lokey via a browser-based fetch.
- Downstream Claude Cowork scheduled task to read this script's digest output and apply judgement-layer classification/summarisation.

## License

Internal i2 Capital Markets automation project — not for external distribution.
