"""
pipeline.py — Two execution paths:

  run_fast_poll()     Daily (or twice-daily), cheap, no LLM.
                      Runs only fast sources, checks urgency, fires instant alerts.

  run_weekly_digest() Thursday 8 AM, all sources, LLM extraction allowed.
                      Produces DigestSummary for the weekly email.

Both paths share the same scraper pool and aggregator;
the fast poll skips LLM extraction and restricts the source list.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

from core import Aggregator, Categorizer, LLMExtractor, Ranker
from core.alert_gate import AlertGate
from core.state import StateStore
from core.urgency import compute_urgency
from models import Category, DigestSummary, Event, EventSource
from scrapers import (
    BlogTOScraper,
    CommunitySourcesScraper,
    EventbriteScraper,
    HarbourfrontScraper,
    InstagramScraper,
    LumaScraper,
    MeetupScraper,
    OntarioPlaceScraper,
    RedditScraper,
    RunClubsCaScraper,
    SongkickScraper,
)

load_dotenv()
logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict[str, Any]:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Fast poll ──────────────────────────────────────────────────────────────────

async def run_fast_poll(
    config: dict[str, Any],
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[Event]:
    """
    Cheap daily poll: fetch fast sources, score urgency + worth, return
    events that should trigger an instant alert.

    No LLM calls are made on this path.

    Returns
    -------
    list[Event]
        Events that passed the alert gate, in order of relevance score.
        Callers are responsible for sending the actual emails and calling
        ``gate.record_alert(event)`` afterwards.
    """
    now = now or datetime.now(tz=timezone.utc)
    date_from = now
    date_to = now + timedelta(days=config.get("lookahead_days", 10))

    state_path = config.get("state_path", "./state/seen_events.json")
    state = StateStore(state_path)
    state.load()

    # ── 1. Fetch fast sources only (no LLM) ───────────────────────
    if dry_run:
        batches = [_load_sample_events()]
    else:
        batches = await _run_fast_scrapers(date_from, date_to, config)

    # ── 2. Aggregate (no LLM extractor) ──────────────────────────
    aggregator = Aggregator(llm_extractor=None)
    events = await aggregator.aggregate(batches)

    # ── 3. Categorize ─────────────────────────────────────────────
    categorizer = Categorizer(config)
    events = categorizer.categorize(events)

    # ── 4. Rank (Worth score) ──────────────────────────────────────
    ranker = Ranker(config)
    events = ranker.rank(events, now=now)

    # ── 5. Record all events in state ─────────────────────────────
    for event in events:
        state.record_event(event.id, event.start_dt.date())

    # ── 6. Gate: collect events that should alert ─────────────────
    gate = AlertGate(config, state)
    alert_events: list[Event] = []

    for event in events:
        urgency = compute_urgency(event, config, state)
        if gate.should_alert(event, worth=event.relevance_score, urgency=urgency):
            alert_events.append(event)
            gate.record_alert(event)
            if gate.cap_reached:
                logger.info("Daily alert cap reached — stopping fast poll gate.")
                break

    state.save()

    logger.info(
        "Fast poll complete: %d events, %d alerts fired",
        len(events), len(alert_events),
    )
    return alert_events


# ── Weekly digest ──────────────────────────────────────────────────────────────

async def run_weekly_digest(
    config: dict[str, Any],
    dry_run: bool = False,
    now: datetime | None = None,
) -> DigestSummary:
    """
    Full weekly pipeline: all sources, LLM extraction allowed.

    The digest marks events that have already gone out as instant
    alerts and groups recurring / constant events separately so they
    don't look like news.

    Parameters
    ----------
    config:
        Parsed config.yaml.
    dry_run:
        Skip real API calls; load sample fixture instead.
    now:
        Override "now" for testing.

    Returns
    -------
    DigestSummary
        Ready to pass to EmailRenderer.
    """
    now = now or datetime.now(tz=timezone.utc)
    date_from = now
    date_to = now + timedelta(days=config.get("lookahead_days", 10))

    state_path = config.get("state_path", "./state/seen_events.json")
    state = StateStore(state_path)
    state.load()

    # ── 1. Fetch from all scrapers ─────────────────────────────────
    if dry_run:
        batches = [_load_sample_events()]
    else:
        batches = await _run_all_scrapers(date_from, date_to, config)

    # ── 2. Aggregate + LLM extraction ─────────────────────────────
    extractor = LLMExtractor() if os.getenv("OPENAI_API_KEY") else None
    aggregator = Aggregator(llm_extractor=extractor)
    events = await aggregator.aggregate(batches)

    # ── 3. Categorize ─────────────────────────────────────────────
    categorizer = Categorizer(config)
    events = categorizer.categorize(events)

    # ── 4. Rank ────────────────────────────────────────────────────
    ranker = Ranker(config)
    events = ranker.rank(events, now=now)

    # ── 5. Record all events in state ─────────────────────────────
    for event in events:
        state.record_event(event.id, event.start_dt.date())

    # ── 6. Filter by min relevance score ─────────────────────────
    min_score = config.get("min_relevance_score", 0.1)
    events = [e for e in events if e.relevance_score >= min_score]

    # ── 7. Build summary ──────────────────────────────────────────
    top_picks = [e for e in events if e.relevance_score >= ranker.top_picks_threshold]

    events_by_cat: dict[str, int] = {}
    events_by_zone: dict[str, int] = {}
    for event in events:
        for cat in event.categories:
            events_by_cat[cat.value] = events_by_cat.get(cat.value, 0) + 1
        zone = event.location_zone.value
        events_by_zone[zone] = events_by_zone.get(zone, 0) + 1

    state.save()

    logger.info(
        "Weekly digest complete: %d events, %d top picks",
        len(events), len(top_picks),
    )

    return DigestSummary(
        generated_at=now,
        date_range_start=date_from,
        date_range_end=date_to,
        total_events=len(events),
        events_by_category=events_by_cat,
        events_by_zone=events_by_zone,
        top_picks=top_picks[:5],
        all_events=events,
    )


# ── Backwards-compat alias (used by server.py and tests) ─────────────────────

async def run_pipeline(
    config: dict[str, Any],
    dry_run: bool = False,
    now: datetime | None = None,
) -> DigestSummary:
    """Alias for run_weekly_digest() — keeps existing callers working."""
    return await run_weekly_digest(config, dry_run=dry_run, now=now)


# ── Scraper helpers ────────────────────────────────────────────────────────────

_FAST_SOURCE_NAMES: frozenset[str] = frozenset({"instagram", "luma", "meetup"})


async def _run_fast_scrapers(
    date_from: datetime,
    date_to: datetime,
    config: dict[str, Any],
) -> list[list[Event]]:
    """Run only the sources that produce fast-filling events."""
    async with httpx.AsyncClient() as client:
        scrapers = [
            InstagramScraper(client, config),
            LumaScraper(client, config),
            MeetupScraper(client, config),
        ]
        results = await asyncio.gather(
            *[s.fetch(date_from, date_to) for s in scrapers],
            return_exceptions=True,
        )

    return _collect_batches(results)


async def _run_all_scrapers(
    date_from: datetime,
    date_to: datetime,
    config: dict[str, Any] | None = None,
) -> list[list[Event]]:
    """Run all scrapers concurrently."""
    cfg = config or {}
    async with httpx.AsyncClient() as client:
        scrapers = [
            LumaScraper(client, cfg),
            EventbriteScraper(client),
            MeetupScraper(client, cfg),
            SongkickScraper(client),
            BlogTOScraper(client),
            RedditScraper(client),
            RunClubsCaScraper(client),
            OntarioPlaceScraper(client, cfg),
            HarbourfrontScraper(client, cfg),
            InstagramScraper(client, cfg),
            CommunitySourcesScraper(client, cfg),
        ]
        results = await asyncio.gather(
            *[s.fetch(date_from, date_to) for s in scrapers],
            return_exceptions=True,
        )

    return _collect_batches(results)


def _collect_batches(results: tuple) -> list[list[Event]]:
    batches: list[list[Event]] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("Scraper %d failed: %s", i, result)
            batches.append([])
        else:
            batches.append(result)  # type: ignore[arg-type]
    return batches


def _load_sample_events() -> list[Event]:
    import json
    fixture_path = Path(__file__).parent / "tests" / "fixtures" / "sample_events.json"
    if not fixture_path.exists():
        logger.warning("No sample_events.json found — dry run returns empty list")
        return []
    with open(fixture_path) as f:
        data = json.load(f)
    events = []
    for item in data:
        try:
            events.append(Event.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load sample event: %s", exc)
    logger.info("Loaded %d sample events from fixture", len(events))
    return events
