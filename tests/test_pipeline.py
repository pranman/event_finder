"""
tests/test_pipeline.py — Integration test for the full pipeline using sample fixtures.

Runs the full pipeline in dry-run mode (no real API calls) and validates
the DigestSummary output end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from pipeline import run_pipeline
from models import Category, DigestSummary


class TestPipelineDryRun:
    """End-to-end pipeline test using fixture data (no API keys required)."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_digest_summary(self, config, now):
        digest = await run_pipeline(config, dry_run=True, now=now)
        assert isinstance(digest, DigestSummary)

    @pytest.mark.asyncio
    async def test_dry_run_has_events(self, config, now):
        digest = await run_pipeline(config, dry_run=True, now=now)
        assert digest.total_events > 0

    @pytest.mark.asyncio
    async def test_dry_run_events_are_categorized(self, config, now):
        digest = await run_pipeline(config, dry_run=True, now=now)
        for event in digest.all_events:
            assert len(event.categories) > 0

    @pytest.mark.asyncio
    async def test_dry_run_events_are_scored(self, config, now):
        digest = await run_pipeline(config, dry_run=True, now=now)
        for event in digest.all_events:
            assert 0.0 <= event.relevance_score <= 1.0

    @pytest.mark.asyncio
    async def test_dry_run_all_events_have_valid_datetimes(self, config, now):
        """All events must have parseable, non-null start_dt."""
        digest = await run_pipeline(config, dry_run=True, now=now)
        for event in digest.all_events:
            assert event.start_dt is not None
            assert isinstance(event.start_dt.year, int)

    @pytest.mark.asyncio
    async def test_dry_run_top_picks_have_high_scores(self, config, now):
        digest = await run_pipeline(config, dry_run=True, now=now)
        from core.ranker import Ranker
        ranker = Ranker(config)
        for pick in digest.top_picks:
            assert pick.relevance_score >= ranker.top_picks_threshold

    @pytest.mark.asyncio
    async def test_dry_run_date_range_is_10_days(self, config, now):
        digest = await run_pipeline(config, dry_run=True, now=now)
        delta = digest.date_range_end - digest.date_range_start
        assert delta.days == config.get("lookahead_days", 10)
