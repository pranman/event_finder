# events_finder — Architecture

## What it does

Two execution paths, two cadences:

- **Daily fast poll (9 AM)** — cheap, no LLM. Fetches Instagram/Luma/Meetup
  only, detects urgency, fires an instant email when an event is high on both
  *worth* and *urgency* (think: free yoga that fills in 2 hours).
- **Weekly digest (Thursday 8 AM)** — all sources, LLM extraction allowed.
  Produces the full ranked digest email.

The overwhelm valve: an instant alert only fires when an event scores high on
**both** axes. Everything else waits for Thursday.

```
                URGENT (fills fast / one-time)
                       │
  skip / digest   ◄────┼────►   ⚡ INSTANT ALERT
                       │         (top-right quadrant only)
  ─────────────────────┼──────────────────────
                       │
  ignore               │   weekly digest
              NOT URGENT (recurring / constant)
```

---

## The key insight about data sources

```
┌─────────────────────────────────────────────────────────────────┐
│  TYPE 1 — Structured APIs                                       │
│  Luma, Eventbrite, Meetup, Songkick                             │
│                                                                 │
│  Clean JSON. Ask "events near Toronto June 7–17", get back      │
│  structured data with titles, dates, coordinates. No AI needed. │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  TYPE 2 — Regular websites                                      │
│  Ontario Place, Harbourfront Centre, BlogTO, runclubs.ca        │
│                                                                 │
│  Plain HTML. We fetch, strip noise, pass text to OpenAI (weekly │
│  path only). OpenAI is the parser, not the knowledge source.    │
│  A keyword pre-filter skips the LLM call when text has no       │
│  event-signal keywords — biggest lever on OpenAI spend.         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  TYPE 3 — Social / session-based                                │
│  Instagram (@notarunclub.to, @wellnessto.events)                │
│  Reddit (r/toronto)                                             │
│                                                                 │
│  Require a session or API key. Events buried in freeform        │
│  captions/posts. Instagram is on the fast path (fast_alert:     │
│  true). Reddit is weekly-only (LLM extraction).                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pipeline flow

### Fast path — daily 9 AM (no LLM)

```
Daily 9 AM (cron)
        │
        ▼
┌───────────────────────────────────┐
│  Fast scrapers only               │
│  • Instagram (fast_alert accounts)│
│  • Luma (watched hosts + geo)     │
│  • Meetup                         │
└──────────────┬────────────────────┘
               │
               ▼
┌───────────────────────────────────┐
│  Aggregator (no LLM)              │
│  • 3-pass dedup:                  │
│    1. Exact ID (SHA-1 title+date) │
│    2. Coord-based (≥0.75 sim)     │
│    3. Title similarity ≥ 0.90     │
└──────────────┬────────────────────┘
               │
               ▼
┌───────────────────────────────────┐
│  Categorizer + Ranker (Worth)     │
└──────────────┬────────────────────┘
               │
               ▼
┌───────────────────────────────────┐
│  State — record_event()           │
│  Detects: new / recurring /       │
│  already alerted                  │
└──────────────┬────────────────────┘
               │
               ▼
┌───────────────────────────────────┐
│  Urgency scorer (no LLM)          │
│  Keywords in text     +0.30       │
│  Free + fitness       +0.20       │
│  fast_alert source    +0.40       │
│  Not new → 0.0                    │
│  Recurring → 0.0                  │
└──────────────┬────────────────────┘
               │
               ▼
┌───────────────────────────────────┐
│  Alert gate (ALL must hold):      │
│  • worth  ≥ 0.55                  │
│  • urgency ≥ 0.60                 │
│  • new this run, not recurring    │
│  • not already alerted            │
│  • daily cap not hit (max 2)      │
└──────────────┬────────────────────┘
               │  qualifying events
               ▼
         ⚡ Instant alert email (one per event)
```

### Weekly path — Thursday 8 AM (LLM allowed)

```
Thursday 8 AM (cron)
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  All scrapers run concurrently (asyncio.gather)                 │
│  TYPE 1: Luma, Eventbrite, Meetup, Songkick                     │
│  TYPE 2: Ontario Place, Harbourfront, BlogTO, runclubs.ca       │
│  TYPE 3: Instagram, Reddit                                      │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  Aggregator (LLM extraction on raw-text events)                 │
│  • Keyword pre-filter before LLM call (cuts API spend)          │
│  • 3-pass dedup (same as fast path)                             │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  Categorizer → Ranker → State.record_event()                    │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  Weekly email digest (Jinja2 → Gmail SMTP)                      │
│  • Top Picks  (worth ≥ 0.55)                                    │
│  • Per-category cards                                           │
│  • Zone map summary                                             │
│  → moaddeli.m@gmail.com                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Ranker — Worth score signals

| Signal | Weight |
|--------|--------|
| Interest category match (Tech / Concerts / Running) | 40% |
| Proximity to Fort York | 30% |
| Free / low price | 10% |
| Happening this weekend | 10% |
| Rich info (description + image) | 5% |
| Named venue | 5% |

Top picks threshold: **0.55**. Alert worth threshold: **0.55**.

---

## State store (`core/state.py`)

Persistent JSON file at `state_path` (default `./state/seen_events.json`).
Loaded at pipeline start, saved at exit.

Per event:

| Field | Purpose |
|-------|---------|
| `event_key` | SHA-1(normalized title + date) — stable cross-source ID |
| `first_seen` | Timestamp of first run that observed this event |
| `times_seen` | Increment each run — ≥ 3 with multiple dates → recurring |
| `observed_dates` | List of distinct dates seen on |
| `alerted_at` | Timestamp if instant alert sent; null otherwise |

---

## File structure

```
events_finder/
│
├── config.yaml              ← Preferences, thresholds, state path, cron schedules
├── pipeline.py              ← run_fast_poll() + run_weekly_digest()
├── server.py                ← MCP entry point (4 tools)
│
├── state/
│   └── seen_events.json     ← Persistent event state (auto-created on first run)
│
├── scrapers/
│   ├── base.py              ← Abstract base (error handling, HTTP client)
│   ├── luma.py              ← Geo discovery + watched host profiles
│   ├── eventbrite.py        ← Eventbrite REST API
│   ├── meetup.py            ← Meetup GraphQL
│   ├── songkick.py          ← Songkick concerts
│   ├── ontarioplace.py      ← Ontario Place events + yoga + summer series
│   ├── harbourfront.py      ← Harbourfront Centre what's on
│   ├── blogto.py            ← BlogTO event listings
│   ├── runclubs.py          ← runclubs.ca Toronto directory
│   ├── instagram.py         ← @notarunclub.to, @wellnessto.events
│   └── reddit.py            ← r/toronto via PRAW
│
├── core/
│   ├── state.py             ← Seen-events store: new / recurring / alerted detection
│   ├── urgency.py           ← Cheap urgency scorer (no LLM)
│   ├── alert_gate.py        ← Worth + Urgency + state + daily cap → alert decision
│   ├── aggregator.py        ← Merge + 3-pass deduplicate across all scrapers
│   ├── categorizer.py       ← Keyword tags + Haversine zones
│   ├── ranker.py            ← 5-signal Worth scorer
│   └── llm_extractor.py     ← OpenAI: raw text → structured events (weekly only)
│
├── email_digest/
│   ├── template.html        ← Dark-mode weekly digest email
│   ├── alert_template.html  ← Single-event instant-alert email
│   ├── renderer.py          ← render() for digest, render_alert() for instant alerts
│   └── sender.py            ← Gmail SMTP
│
├── models/event.py          ← Canonical Event shape (Pydantic)
└── scheduler/setup_cron.py  ← Two cron entries: daily fast poll + Thursday digest
```

---

## Your data sources

| Source | Type | Path | What it covers |
|--------|------|------|----------------|
| Luma (geo) | API | Fast + Weekly | Any Luma event within 20 km of downtown |
| Luma host `usr-xHOhElhEccTFQVa` | API | Fast + Weekly | Run + paddle organizer |
| Eventbrite | API | Weekly | Broad event coverage across Toronto |
| Meetup | API | Fast + Weekly | Community groups, run clubs, tech meetups |
| Songkick | API | Weekly | Concerts and live music |
| Ontario Place | Website | Weekly | Yoga in Trillium Park, summer series |
| Harbourfront Centre | Website | Weekly | Free waterfront concerts, outdoor movies |
| BlogTO | Website | Weekly | Editorial picks across the city |
| runclubs.ca | Website | Weekly | Recurring Toronto run clubs |
| @notarunclub.to | Instagram | Fast + Weekly | Monday runs |
| @wellnessto.events | Instagram | Fast + Weekly | Free yoga / wellness (fast_alert: true) |
| r/toronto | Reddit | Weekly | Community-posted events off official calendars |

---

## Anti-overwhelm knobs (all in `config.yaml`)

1. **High bar** — both `worth_threshold` (0.55) and `urgency_threshold` (0.60) must clear.
2. **Daily cap** — `max_alerts_per_day: 2`. Extras queue into Thursday's digest.
3. **Alert-once memory** — `alerted_at` in state; no event ever pings twice.
4. **Recurring filter** — events seen ≥ 3 times on multiple dates are flagged as recurring and never alert.

---

## Adding a new source

**New API source:** implement `BaseScraper._fetch()` in `scrapers/`, add to
`scrapers/__init__.py` and the appropriate scraper list in `pipeline.py`.

**New website:** add a section to `config.yaml` and a scraper class following
the Ontario Place pattern (or wait for the planned `GenericWebsiteScraper` refactor).

**New Instagram account:** add one entry to `instagram_accounts` in `config.yaml`.
Set `fast_alert: true` to include it on the daily fast path.

**New Luma host:** add one entry to `luma_hosts` in `config.yaml`. No code needed.
