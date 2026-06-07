"""
tests/conftest.py — Shared pytest fixtures.

Fixtures here are available to all test files without import.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from models import Category, Event, EventSource, LocationZone

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ──────────────────────────────────────────────
# Datetime helpers
# ──────────────────────────────────────────────

@pytest.fixture
def now() -> datetime:
    """Fixed reference time used across all tests (Thursday morning)."""
    return datetime(2026, 6, 12, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def date_from(now: datetime) -> datetime:
    return now


@pytest.fixture
def date_to(now: datetime) -> datetime:
    from datetime import timedelta
    return now + timedelta(days=10)


# ──────────────────────────────────────────────
# Event factories
# ──────────────────────────────────────────────

def make_event(
    title: str = "Test Event",
    start_dt: datetime | None = None,
    source: EventSource = EventSource.SAMPLE,
    lat: float | None = 43.6385,
    lng: float | None = -79.4028,
    price: str = "Free",
    categories: list[Category] | None = None,
    location_zone: LocationZone = LocationZone.FORT_YORK,
    **kwargs,
) -> Event:
    """Factory function for creating test Event objects."""
    if start_dt is None:
        start_dt = datetime(2026, 6, 14, 19, 0, 0, tzinfo=timezone.utc)
    return Event(
        id=Event.make_id(title, start_dt),
        title=title,
        start_dt=start_dt,
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        source=source,
        lat=lat,
        lng=lng,
        price=price,
        categories=categories or [Category.OTHER],
        location_zone=location_zone,
        **kwargs,
    )


@pytest.fixture
def sample_tech_event(now: datetime) -> Event:
    from datetime import timedelta
    return make_event(
        title="Toronto AI Meetup",
        start_dt=now + timedelta(days=1),
        source=EventSource.LUMA,
        categories=[Category.TECH],
        location_zone=LocationZone.DOWNTOWN_CORE,
        venue_name="MaRS Discovery District",
        address="101 College St, Toronto",
        lat=43.6594,
        lng=-79.3895,
        description="Monthly AI gathering",
        price="Free",
    )


@pytest.fixture
def sample_concert_event(now: datetime) -> Event:
    from datetime import timedelta
    return make_event(
        title="Indie Night at Lee's Palace",
        start_dt=now + timedelta(days=2),
        source=EventSource.SONGKICK,
        categories=[Category.CONCERTS],
        location_zone=LocationZone.MIDTOWN,
        venue_name="Lee's Palace",
        address="529 Bloor St W",
        lat=43.6651,
        lng=-79.4100,
        price="$18",
    )


@pytest.fixture
def sample_running_event(now: datetime) -> Event:
    from datetime import timedelta
    return make_event(
        title="Fort York Saturday Run Club",
        start_dt=now + timedelta(days=2),  # Saturday
        source=EventSource.MEETUP,
        categories=[Category.RUNNING],
        location_zone=LocationZone.FORT_YORK,
        venue_name="Coronation Park",
        address="711 Lake Shore Blvd W",
        lat=43.6350,
        lng=-79.4100,
        price="Free",
    )


@pytest.fixture
def all_sample_events(
    sample_tech_event: Event,
    sample_concert_event: Event,
    sample_running_event: Event,
) -> list[Event]:
    return [sample_tech_event, sample_concert_event, sample_running_event]


# ──────────────────────────────────────────────
# Config fixture
# ──────────────────────────────────────────────

@pytest.fixture
def config() -> dict:
    """Load the real config.yaml for integration-level tests."""
    import yaml
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


# ──────────────────────────────────────────────
# Fixture-file loader
# ──────────────────────────────────────────────

@pytest.fixture
def sample_events_from_file() -> list[Event]:
    """Load events from tests/fixtures/sample_events.json."""
    fixture = _FIXTURE_DIR / "sample_events.json"
    with open(fixture) as f:
        data = json.load(f)
    return [Event.model_validate(item) for item in data]
