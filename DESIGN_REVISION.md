# events_finder — Design Revision

> **Purpose of this doc:** a revision to `ARCHITECTURE.md`, written to be handed
> to an implementer. It does not replace the existing pipeline — it reshapes it
> around the real goal: **don't miss events that fill up, without drowning in
> alerts, and without burning OpenAI on every poll.**

---

## 1. The core idea: two axes, not one score

The current `ranker.py` collapses everything into a single relevance score. That
answers only one question. We actually have two independent questions:

1. **Worth** — do I care about this event? (relevance to interests + location)
2. **Urgency** — will I miss it if I wait for the Thursday digest? (scarce /
   one-time / fills fast)

An **instant alert fires only when an event is high on BOTH axes.** Everything
else waits for the weekly digest. This is the overwhelm valve.

```
                 URGENT (fills fast / one-time)
                        │
   skip / digest   ◄────┼────►   ⚡ INSTANT ALERT
                        │         (only the top-right
   ─────────────────────┼──────── quadrant pings you)
                        │
   ignore               │   weekly digest
                        │
                 NOT URGENT (recurring / constant)
```

`ranker.py` stays — it computes **Worth**. We add a separate **Urgency** signal
and a gate that combines them.

---

## 2. Constant vs. fills-fast (the cheap, no-LLM distinction)

The single most useful classification, and it needs **zero OpenAI calls**:

- **Recurring / constant** — same title+venue+weekday seen across multiple runs
  (e.g. a Monday run club). Never alert. List once in the digest. These never
  "fill" in a way you'd miss.
- **One-time / scarce** — first time we've seen it, has a specific single date,
  free or capacity-limited. **This is the only thing alert-worthy.**

Detecting "recurring" requires **state** — see §4. This is the missing piece in
the current architecture and is required regardless of alerts.

---

## 3. The Urgency signal (cheap signals, LLM only as last resort)

Compute an urgency score from structured + keyword signals. **Do not call OpenAI
for this.**

| Signal | Source | Effect |
|--------|--------|--------|
| Caption/desc contains "limited", "limited spots", "RSVP", "first come", "sign up", "spots left" | keyword | ↑ urgency |
| Has a single specific date (not "every Monday" / recurring) | structured | ↑ urgency |
| Free + popular venue type (yoga / wellness / fitness) | category + price | ↑ urgency |
| Source pre-tagged `fast_alert: true` in config (e.g. `@wellnessto.events`) | config | ↑ urgency (trust it) |
| Event seen before in state | state | ↓ to zero (already known) |
| Recurring (per §2) | state | ↓ to zero (never urgent) |

LLM is allowed **only** when the cheap signals are ambiguous AND the event has
already passed the Worth threshold — i.e. on a handful of events per poll, not
all of them. This is the fix for "overusing OpenAI."

---

## 4. State (new — required)

Add a persistent store of what we've already seen and already alerted on. A JSON
file or tiny SQLite db is enough; no server needed.

Per event, track:
- `event_key` — stable identity (see §5 dedup)
- `first_seen` — timestamp first observed
- `times_seen` — counter (used to detect recurring)
- `last_weekday` / observed dates — used to detect recurring cadence
- `alerted_at` — timestamp if an instant alert was sent (else null)

Used for:
- **Recurring detection** — `times_seen` high and on a regular cadence → constant.
- **New-event detection** — `first_seen == this run` → candidate for alert.
- **Alert-once** — never alert if `alerted_at` is set.

---

## 5. Deduplication (tighten — current "fuzzy title match" is fragile)

Same event from two sources must collapse to one `event_key`, deterministically:

1. Exact provider ID match (within same source).
2. Cross-source key: normalized(title) + date + rounded venue coords.
3. Fuzzy title only as a last tie-breaker, with a high similarity threshold.

When merging duplicates, keep the **best** record (richest info / best link) and
preserve `first_seen` from the earliest sighting. Source priority decides which
*link* wins, not whether the event is dropped.

---

## 6. Two paths: fast poll vs. weekly digest

### Fast path — frequent, cheap, no LLM
- **Cadence:** daily or twice-daily (configurable, default once/day).
- **Sources:** only the ones that actually produce fast-filling events. Likely
  just `@wellnessto.events`, watched Luma hosts, and Meetup. **Confirm the source
  list before building** — do not run websites or Reddit on the fast path.
- **Work:** fetch structured data → dedup against state → compute Worth (existing
  ranker) and Urgency (§3) → gate.
- **Gate to send an instant alert, ALL must hold:**
  - `worth >= WORTH_THRESHOLD`
  - `urgency >= URGENCY_THRESHOLD`
  - event is new (`first_seen == this run`) and not recurring
  - `alerted_at` is null
  - daily alert cap not yet hit (§7)
- **Output:** a single short email per qualifying event (or a small batch).

### Weekly path — Thursday 8 AM, LLM allowed
- Keep the existing pipeline essentially as-is: all sources, LLM extraction on
  website/Reddit raw text, categorize, rank, render digest.
- The digest now also reflects state: mark which items already went out as
  instant alerts ("already sent you this"), and group the constant/recurring
  items separately so they don't look like news.

---

## 7. Anti-overwhelm knobs (all config-driven)

1. **High bar** — both thresholds must clear; when in doubt it waits for Thursday.
2. **Daily cap** — e.g. `max_alerts_per_day: 2`. Beyond the cap, queue extras into
   the next digest instead of pinging.
3. **Alert-once memory** — §4 `alerted_at`, so no event (and no recurring class)
   ever pings twice.

---

## 8. Config additions (`config.yaml`)

```yaml
alerts:
  enabled: true
  worth_threshold: 0.55
  urgency_threshold: 0.6
  max_alerts_per_day: 2
  fast_poll_cron: "0 9 * * *"      # daily 9 AM
  # only these sources run on the fast path:
  fast_sources:
    - instagram:@wellnessto.events
    - luma_hosts
    - meetup

urgency_keywords:
  - limited
  - "limited spots"
  - "spots left"
  - rsvp
  - "first come"
  - "sign up"

state_path: ./state/seen_events.json
```

Per-source `fast_alert: true` already exists on Instagram accounts — reuse it to
populate `fast_sources` rather than duplicating.

---

## 9. What changes, file by file

| File | Change |
|------|--------|
| `core/ranker.py` | Keep as **Worth** scorer. No change to its math needed. |
| `core/urgency.py` | **New.** Cheap urgency scorer (§3). No OpenAI. |
| `core/state.py` | **New.** Load/save seen-events store; recurring + new detection (§4). |
| `core/aggregator.py` | Tighten dedup → stable `event_key` (§5); merge keeps best + earliest `first_seen`. |
| `core/alert_gate.py` | **New.** Combine Worth + Urgency + state + daily cap → decide alert (§6 gate). |
| `pipeline.py` | Split into `run_fast_poll()` (cheap, no LLM, alert path) and `run_weekly_digest()` (existing). |
| `email_digest/` | Add a short single-event "instant alert" template alongside the weekly one. |
| `scheduler/setup_cron.py` | Register **two** cron entries: daily fast poll + Thursday digest. |
| `core/llm_extractor.py` | Now only called on the weekly path. Add a keyword pre-filter so the LLM only sees text likely to contain events. |

---

## 10. Build order (suggested)

1. **State store** (`core/state.py`) — nothing else works without it.
2. **Tighten dedup** in aggregator to produce stable `event_key`.
3. **Urgency scorer** (`core/urgency.py`).
4. **Alert gate** (`core/alert_gate.py`) + daily cap.
5. **Split pipeline** into fast vs weekly; wire the fast path to the gate.
6. **Instant-alert email template** + second cron entry.
7. **Pre-filter** before `llm_extractor` to cut OpenAI usage on the weekly path.

---

## 11. Open questions to resolve before implementing

- **Which sources actually produce fast-filling events?** Confirm the
  `fast_sources` list. Best guess: `@wellnessto.events`, watched Luma hosts,
  Meetup. Probably **not** the websites or Reddit.
- **Fast poll cadence** — once a day enough, or twice? Tune `urgency_threshold`
  against real output for a week or two before trusting it.
- **Instagram fragility** — it's the highest-value, fastest-filling source and the
  most likely to break silently. Add a heartbeat/health check so a broken IG
  session surfaces instead of just producing zero events.
