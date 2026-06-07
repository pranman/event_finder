"""
tests/test_models.py — Tests for the Event Pydantic model.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from models import Category, Event, EventSource, LocationZone
from tests.conftest import make_event


class TestEventMakeId:
    """Event.make_id() should produce stable, collision-resistant IDs."""

    def test_same_title_same_date_gives_same_id(self):
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        id1 = Event.make_id("Toronto AI Meetup", dt)
        id2 = Event.make_id("Toronto AI Meetup", dt)
        assert id1 == id2

    def test_different_titles_give_different_ids(self):
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        assert Event.make_id("Event A", dt) != Event.make_id("Event B", dt)

    def test_different_dates_give_different_ids(self):
        dt1 = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 15, 19, 0, tzinfo=timezone.utc)
        assert Event.make_id("Same Event", dt1) != Event.make_id("Same Event", dt2)

    def test_time_difference_same_date_gives_same_id(self):
        """IDs are date-based only — time changes don't create duplicates."""
        dt1 = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 14, 20, 30, tzinfo=timezone.utc)  # same day, later time
        assert Event.make_id("Toronto AI Meetup", dt1) == Event.make_id("Toronto AI Meetup", dt2)

    def test_id_is_16_chars(self):
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        assert len(Event.make_id("Test", dt)) == 16

    def test_case_insensitive(self):
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        assert Event.make_id("TORONTO AI MEETUP", dt) == Event.make_id("toronto ai meetup", dt)


class TestEventComputedFields:
    """Computed properties on Event."""

    def test_day_label(self):
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)  # Sunday Jun 14 2026
        event = make_event(start_dt=dt)
        assert "Sunday" in event.day_label
        assert "Jun" in event.day_label
        assert "14" in event.day_label

    def test_time_label(self):
        dt = datetime(2026, 6, 14, 19, 30, tzinfo=timezone.utc)
        event = make_event(start_dt=dt)
        assert "7:30 PM" in event.time_label

    def test_is_free_for_free_events(self):
        assert make_event(price="Free").is_free is True
        assert make_event(price="free").is_free is True
        assert make_event(price="0").is_free is True
        assert make_event(price="$0").is_free is True

    def test_is_not_free_for_paid_events(self):
        assert make_event(price="$20").is_free is False
        assert make_event(price="Unknown").is_free is False

    def test_title_whitespace_stripped(self):
        event = make_event(title="  Toronto Meetup  ")
        assert event.title == "Toronto Meetup"


class TestEventValidation:
    """Event Pydantic validation."""

    def test_valid_event_creation(self):
        dt = datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)
        event = Event(
            id=Event.make_id("Test", dt),
            title="Test Event",
            start_dt=dt,
            url="https://example.com",
            source=EventSource.SAMPLE,
        )
        assert event.title == "Test Event"
        assert event.city == "Toronto"  # default
        assert event.categories == []  # default

    def test_default_location_zone(self):
        event = make_event()
        assert event.location_zone == LocationZone.FORT_YORK  # from fixture default
