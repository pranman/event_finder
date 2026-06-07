# events_finder

A personal Toronto event digest. Scrapes events from multiple sources, ranks them by relevance to your location and interests, and emails you a weekly digest every Thursday. Also fires instant alerts for high-urgency free events that fill up fast.

---

## What it does

- **Weekly digest** — every Thursday 8 AM, pulls events from all sources, scores and ranks them, sends an HTML email to your inbox
- **Daily fast poll** — every day at 9 AM, checks fast-moving sources (Luma, Meetup, Instagram) and sends an instant alert if something urgent and high-value appears (e.g. free yoga that fills up in 2 hours)

---

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure your preferences

Edit `config.yaml`:
- `user.email` — where to send the digest
- `user.home` — your home coordinates (used for proximity scoring)
- `categories` — keywords for tech / concerts / running
- `instagram_accounts` — IG accounts to watch
- `luma_hosts` — specific Luma organizers to follow
- `meetup_groups` — specific Meetup groups to pin

### 3. Add secrets

Copy `.env` and fill in the values:

```bash
# Required to send email
GMAIL_SENDER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Google Account → Security → App passwords

# Optional — enables those scrapers
EVENTBRITE_API_KEY=
SONGKICK_API_KEY=
MEETUP_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
LUMA_API_KEY=
OPENAI_API_KEY=        # enables LLM extraction from Reddit/BlogTO raw text
```

**Getting a Gmail App Password:**
1. Go to myaccount.google.com → Security → 2-Step Verification
2. Scroll to the bottom → App passwords
3. Create one named "events_finder", paste the 16-char code above

---

## Running

```bash
# Test the full pipeline (uses real scrapers, sends real email)
python -c "
import asyncio
from pipeline import load_config, run_pipeline
from email_digest.renderer import EmailRenderer
from email_digest.sender import GmailSender
from dotenv import load_dotenv
load_dotenv()

async def send():
    config = load_config()
    digest = await run_pipeline(config, dry_run=False)
    print(f'{digest.total_events} events found')
    renderer = EmailRenderer()
    recipient = config['user']['email']
    html = renderer.render(digest, user_email=recipient)
    date_range = f\"{digest.date_range_start.strftime('%b %-d')}–{digest.date_range_end.strftime('%b %-d')}\"
    subject = GmailSender.build_subject(digest.total_events, date_range)
    GmailSender().send(to=recipient, subject=subject, html_body=html)

asyncio.run(send())
"

# Dry run — uses fixture data, no real API calls
python server.py --test

# Start the MCP server (for Claude Desktop / Gemini integration)
python server.py
```

```bash
# Run tests
pytest
```

---

## Source status

| Source | Status | What's needed |
|---|---|---|
| Harbourfront Centre | ✅ Working | Nothing |
| Ontario Place | ✅ Working | Nothing |
| BlogTO | ✅ Working | Nothing |
| The Bentway, STACKT, Fort York, etc. | ✅ Working | Nothing |
| Luma | ⚠️ Needs key | `LUMA_API_KEY` in `.env` (free, get from lu.ma → Settings → API) |
| Eventbrite | ⚠️ Needs key | `EVENTBRITE_API_KEY` (free at eventbrite.com/platform/api) |
| Meetup | ⚠️ Needs key | `MEETUP_API_KEY` (OAuth token from meetup.com/meetup_api) |
| Songkick | ⚠️ Needs key | `SONGKICK_API_KEY` (free at songkick.com/developer) |
| Reddit | ⚠️ Needs key | `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` (free app at reddit.com/prefs/apps) |
| Instagram | ⚠️ Needs login | Run `instaloader --login <your_ig_username>`, set `INSTAGRAM_SESSION_FILE` in `.env` |
| LLM extraction | ✅ Working | `OPENAI_API_KEY` already set — extracts events from Reddit/BlogTO raw text |

Without any API keys: ~150 events from the working scrapers. With all keys: expected 300–400+.

---

## Project structure

```
events_finder/
├── server.py              # MCP server (5 tools: fetch_events, preview_digest, etc.)
├── pipeline.py            # Orchestrates fast poll + weekly digest
├── config.yaml            # User preferences (location, categories, sources)
├── .env                   # Secrets — never commit
│
├── scrapers/              # One file per source
│   ├── base.py            # Abstract base — all scrapers return list[Event]
│   ├── luma.py
│   ├── eventbrite.py
│   ├── meetup.py
│   ├── songkick.py
│   ├── blogto.py
│   ├── reddit.py
│   ├── runclubs.py
│   ├── harbourfront.py
│   ├── ontarioplace.py
│   ├── instagram.py
│   └── community_sources.py
│
├── core/
│   ├── aggregator.py      # Merges + deduplicates across sources
│   ├── categorizer.py     # Tags events (tech / concerts / running / other) + assigns zone
│   ├── ranker.py          # Scores events 0–1 by relevance
│   ├── llm_extractor.py   # OpenAI extraction for unstructured text
│   ├── urgency.py         # Urgency score for fast-poll alerts
│   ├── alert_gate.py      # Daily alert cap + dedup against already-sent
│   └── state.py           # Persistent seen-events store (JSON)
│
├── email_digest/
│   ├── renderer.py        # Jinja2 HTML email renderer
│   ├── sender.py          # Gmail SMTP sender
│   ├── template.html      # Weekly digest template
│   └── alert_template.html# Instant alert template
│
├── models/                # Pydantic models (Event, DigestSummary, etc.)
├── scheduler/             # APScheduler jobs (weekly digest + fast poll)
├── state/                 # Runtime state (seen_events.json)
└── tests/                 # 79 tests, all passing
```

---

## Known issues / not yet done

- **Instagram requires a saved session** — unauthenticated access is blocked by Instagram (403). Fix: `instaloader --login <username>` then set `INSTAGRAM_SESSION_FILE` in `.env`
- **Scheduler not wired up** — `scheduler/` directory exists but the APScheduler jobs are not connected to the pipeline. The digest and fast poll need to be triggered manually or via cron for now
- **No deduplication across weeks** — the state store tracks seen events but the weekly digest doesn't yet filter out events that already appeared in a previous digest
- **LLM model name** — currently set to `gpt-5.4-mini` in `llm_extractor.py`; update if the model ID changes
