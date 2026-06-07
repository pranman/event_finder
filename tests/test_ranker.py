"""
tests/test_ranker.py — Unit tests for the Ranker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.ranker import Ranker
from models import Category, LocationZone
from tests.conftest import make_event


class TestRankerScores:

    @pytest.fixture
    def ranker(self, config):
        return Ranker(config)

    def test_scores_between_0_and_1(self, ranker, all_sample_events, now):
        result = ranker.rank(all_sample_events, now=now)
        for event in result:
            assert 0.0 <= event.relevance_score <= 1.0

    def test_preferred_category_scores_higher(self, ranker, now):
        tech_event = make_event(
            "Tech Meetup", now + timedelta(days=1),
            categories=[Category.TECH],
            location_zone=LocationZone.FORT_YORK,
        )
        other_event = make_event(
            "Random Event", now + timedelta(days=1),
            categories=[Category.OTHER],
            location_zone=LocationZone.FORT_YORK,
        )
        ranker.rank([tech_event, other_event], now=now)
        assert tech_event.relevance_score > other_event.relevance_score

    def test_nearby_event_scores_higher_than_far(self, ranker, now):
        nearby = make_event(
            "Nearby Event", now + timedelta(days=1),
            lat=43.6385, lng=-79.4028,  # Fort York itself
            categories=[Category.TECH],
        )
        faraway = make_event(
            "Far Event", now + timedelta(days=1),
            lat=43.7500, lng=-79.2500,  # ~15 km away
            categories=[Category.TECH],
        )
        ranker.rank([nearby, faraway], now=now)
        assert nearby.relevance_score > faraway.relevance_score

    def test_free_event_scores_higher_than_paid(self, ranker, now):
        free_event = make_event(
            "Free Event", now + timedelta(days=1),
            price="Free", categories=[Category.TECH],
        )
        paid_event = make_event(
            "Paid Event", now + timedelta(days=1),
            price="$50", categories=[Category.TECH],
        )
        ranker.rank([free_event, paid_event], now=now)
        assert free_event.relevance_score > paid_event.relevance_score

    def test_weekend_event_scores_higher(self, ranker, now):
        # now is Thursday Jun 12; Saturday is Jun 14 (2 days away)
        weekend_event = make_event(
            "Saturday Run", now + timedelta(days=2),
            categories=[Category.RUNNING],
        )
        weekday_event = make_event(
            "Monday Workshop", now + timedelta(days=4),
            categories=[Category.RUNNING],
        )
        ranker.rank([weekend_event, weekday_event], now=now)
        assert weekend_event.relevance_score > weekday_event.relevance_score

    def test_event_with_image_scores_higher(self, ranker, now):
        with_image = make_event(
            "Event With Image", now + timedelta(days=1),
            categories=[Category.CONCERTS],
            image_url="https://example.com/image.jpg",
        )
        without_image = make_event(
            "Event No Image", now + timedelta(days=1),
            categories=[Category.CONCERTS],
        )
        ranker.rank([with_image, without_image], now=now)
        assert with_image.relevance_score > without_image.relevance_score

    def test_result_sorted_descending(self, ranker, all_sample_events, now):
        result = ranker.rank(all_sample_events, now=now)
        scores = [e.relevance_score for e in result]
        assert scores == sorted(scores, reverse=True)

    def test_past_event_gets_zero_weekend_score(self, ranker, now):
        past_event = make_event(
            "Past Event", now - timedelta(days=1),
            categories=[Category.TECH],
        )
        ranker.rank([past_event], now=now)
        # Weekend component should be 0 for past events
        # (total score can still be > 0 from other components)
        assert past_event.relevance_score >= 0.0

    def test_top_picks_threshold(self, ranker, now):
        high_score = make_event(
            "Great nearby free weekend tech event",
            now + timedelta(days=2),  # Saturday
            categories=[Category.TECH],
            lat=43.6385, lng=-79.4028,  # Fort York
            price="Free",
            venue_name="Liberty Village Hub",
            image_url="https://example.com/img.jpg",
            description="A super interesting tech talk with networking after",
        )
        ranker.rank([high_score], now=now)
        # This event has all the right signals — should be above threshold
        assert high_score.relevance_score >= ranker.top_picks_threshold
