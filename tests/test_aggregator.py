"""
tests/test_aggregator.py — Unit tests for the Aggregator.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from core.aggregator import Aggregator, _title_similarity
from models import Event, EventSource
from tests.conftest import make_event


# ──────────────────────────────────────────────
# Title similarity helper tests
# ──────────────────────────────────────────────

class TestTitleSimilarity:

    def test_identical_titles(self):
        assert _title_similarity("Toronto AI Meetup", "Toronto AI Meetup") == pytest.approx(1.0)

    def test_completely_different_titles(self):
        score = _title_similarity("AI Hackathon", "Jazz Night at Lee's Palace")
        assert score < 0.3

    def test_slight_variation_is_high_similarity(self):
        # "Toronto AI Meetup" vs "Toronto AI Meetup June"
        score = _title_similarity("Toronto AI Meetup", "Toronto AI Meetup June")
        assert score > 0.7

    def test_empty_strings(self):
        assert _title_similarity("", "") == 0.0
        assert _title_similarity("Something", "") == 0.0


# ──────────────────────────────────────────────
# Aggregator deduplication tests
# ──────────────────────────────────────────────

class TestAggregatorDedup:

    @pytest.mark.asyncio
    async def test_no_duplicates_unchanged(self):
        aggregator = Aggregator(llm_extractor=None)
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        events = [
            make_event("Event A", dt),
            make_event("Event B", dt + timedelta(hours=2)),
        ]
        result = await aggregator.aggregate([events])
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_exact_duplicate_removed(self):
        """Same event from two different sources → only one kept."""
        aggregator = Aggregator(llm_extractor=None)
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        # Same title + date = same ID
        event_a = make_event("Toronto AI Meetup", dt, source=EventSource.LUMA)
        event_b = make_event("Toronto AI Meetup", dt, source=EventSource.EVENTBRITE)

        result = await aggregator.aggregate([[event_a], [event_b]])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_more_authoritative_source_wins(self):
        """When deduplicating, Luma wins over Eventbrite."""
        aggregator = Aggregator(llm_extractor=None)
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        luma_event = make_event("Same Concert", dt, source=EventSource.LUMA)
        reddit_event = make_event("Same Concert", dt, source=EventSource.REDDIT)

        result = await aggregator.aggregate([[reddit_event], [luma_event]])
        assert len(result) == 1
        assert result[0].source == EventSource.LUMA

    @pytest.mark.asyncio
    async def test_fuzzy_dedup_catches_variant_title(self):
        """Exact same title (score=1.0) but different IDs on same day → merged."""
        aggregator = Aggregator(llm_extractor=None)
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        e1 = make_event("Toronto AI Meetup", dt, source=EventSource.LUMA)
        # Same title, different ID (e.g. came from two different scrapers with
        # slightly different ID generation) — same date → must be deduped
        e2 = Event(
            id="completely-different-id",
            title="Toronto AI Meetup",   # identical title
            start_dt=dt,
            url="https://eventbrite.com/e/toronto-ai",
            source=EventSource.EVENTBRITE,
        )
        result = await aggregator.aggregate([[e1, e2]])
        # Identical title + same date = similarity 1.0 → merged
        assert len(result) == 1
        # Luma wins (higher authority)
        assert result[0].source == EventSource.LUMA

    @pytest.mark.asyncio
    async def test_fuzzy_dedup_keeps_genuinely_different_titles(self):
        """'Toronto AI Meetup' vs 'Toronto AI Meetup (Monthly)' scores 0.65 → kept separate."""
        aggregator = Aggregator(llm_extractor=None)
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        e1 = make_event("Toronto AI Meetup", dt)
        e2 = Event(
            id="other-id",
            title="Toronto AI Meetup (Monthly)",
            start_dt=dt,
            url="https://example.com/other",
            source=EventSource.EVENTBRITE,
        )
        result = await aggregator.aggregate([[e1, e2]])
        # Similarity ~0.65 < 0.80 threshold → treated as separate events
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_different_dates_not_merged(self):
        """Same-ish title on different days → kept as separate events."""
        aggregator = Aggregator(llm_extractor=None)
        dt1 = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 21, 19, 0, tzinfo=timezone.utc)
        e1 = make_event("Weekly Meetup", dt1)
        e2 = Event(
            id=Event.make_id("Weekly Meetup", dt2),
            title="Weekly Meetup",
            start_dt=dt2,
            url="https://example.com/other",
            source=EventSource.LUMA,
        )
        result = await aggregator.aggregate([[e1, e2]])
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_result_sorted_by_start_dt(self):
        """Output should always be sorted chronologically."""
        aggregator = Aggregator(llm_extractor=None)
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        events = [
            make_event("Event C", dt + timedelta(hours=4)),
            make_event("Event A", dt),
            make_event("Event B", dt + timedelta(hours=2)),
        ]
        result = await aggregator.aggregate([events])
        dates = [e.start_dt for e in result]
        assert dates == sorted(dates)

    @pytest.mark.asyncio
    async def test_empty_batches_handled(self):
        aggregator = Aggregator(llm_extractor=None)
        result = await aggregator.aggregate([[], [], []])
        assert result == []

    @pytest.mark.asyncio
    async def test_raw_text_events_included_without_extractor(self):
        """Events with raw_text but no LLM extractor → included as-is."""
        aggregator = Aggregator(llm_extractor=None)
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        reddit_event = make_event("Reddit Post", dt, raw_text="Some event text")
        result = await aggregator.aggregate([[reddit_event]])
        assert len(result) == 1
        assert result[0].raw_text == "Some event text"
