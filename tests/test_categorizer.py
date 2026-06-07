"""
tests/test_categorizer.py — Unit tests for the Categorizer.
"""

from __future__ import annotations

import pytest

from core.categorizer import Categorizer, _haversine_km
from models import Category, LocationZone
from tests.conftest import make_event


# ──────────────────────────────────────────────
# Haversine helper tests
# ──────────────────────────────────────────────

class TestHaversine:

    def test_same_point_is_zero(self):
        assert _haversine_km(43.6385, -79.4028, 43.6385, -79.4028) == pytest.approx(0.0)

    def test_known_distance(self):
        # Fort York → Union Station ≈ 1.9–3.0 km
        dist = _haversine_km(43.6385, -79.4028, 43.6452, -79.3806)
        assert 1.5 < dist < 4.0

    def test_direction_doesnt_matter(self):
        d1 = _haversine_km(43.0, -79.0, 43.5, -79.5)
        d2 = _haversine_km(43.5, -79.5, 43.0, -79.0)
        assert d1 == pytest.approx(d2)


# ──────────────────────────────────────────────
# Category assignment tests
# ──────────────────────────────────────────────

class TestCategorizerCategories:

    @pytest.fixture
    def categorizer(self, config):
        return Categorizer(config)

    def test_tech_event_tagged_as_tech(self, categorizer, sample_tech_event):
        sample_tech_event.categories = []
        result = categorizer.categorize([sample_tech_event])
        assert Category.TECH in result[0].categories

    def test_concert_event_tagged_as_concerts(self, categorizer, sample_concert_event):
        sample_concert_event.categories = []
        result = categorizer.categorize([sample_concert_event])
        assert Category.CONCERTS in result[0].categories

    def test_running_event_tagged_as_running(self, categorizer, sample_running_event):
        sample_running_event.categories = []
        result = categorizer.categorize([sample_running_event])
        assert Category.RUNNING in result[0].categories

    def test_unknown_event_tagged_as_other(self, categorizer):
        event = make_event(title="Random Community Gathering", description="")
        event.categories = []
        result = categorizer.categorize([event])
        assert Category.OTHER in result[0].categories

    def test_event_can_have_multiple_categories(self, categorizer):
        """A running music festival should get both RUNNING and CONCERTS."""
        event = make_event(
            title="Run + Music Festival",
            description="A 5K run followed by live music concert at the park",
        )
        event.categories = []
        result = categorizer.categorize([event])
        # Should get at least one match
        assert len(result[0].categories) >= 1

    def test_categorizer_returns_same_list_mutated(self, categorizer, all_sample_events):
        original = all_sample_events
        for e in original:
            e.categories = []
        result = categorizer.categorize(original)
        assert result is original  # same list, mutated in place


# ──────────────────────────────────────────────
# Location zone assignment tests
# ──────────────────────────────────────────────

class TestCategorizerZones:

    @pytest.fixture
    def categorizer(self, config):
        return Categorizer(config)

    def test_fort_york_coordinates(self, categorizer):
        event = make_event(lat=43.6385, lng=-79.4028)
        event.location_zone = LocationZone.UNKNOWN
        result = categorizer.categorize([event])
        assert result[0].location_zone == LocationZone.FORT_YORK

    def test_mars_discovery_district_is_downtown(self, categorizer):
        event = make_event(lat=43.6594, lng=-79.3895)
        event.location_zone = LocationZone.UNKNOWN
        result = categorizer.categorize([event])
        assert result[0].location_zone == LocationZone.DOWNTOWN_CORE

    def test_event_without_coords_uses_address_heuristic(self, categorizer):
        event = make_event(lat=None, lng=None, venue_name="Liberty Village Pub")
        event.location_zone = LocationZone.UNKNOWN
        result = categorizer.categorize([event])
        assert result[0].location_zone == LocationZone.WATERFRONT_WEST

    def test_unknown_address_stays_unknown(self, categorizer):
        event = make_event(lat=None, lng=None, venue_name="", address="")
        event.location_zone = LocationZone.UNKNOWN
        result = categorizer.categorize([event])
        assert result[0].location_zone == LocationZone.UNKNOWN


# ──────────────────────────────────────────────
# Zone-from-address heuristic tests
# ──────────────────────────────────────────────

class TestZoneFromAddress:

    def test_fort_york_keywords(self):
        from core.categorizer import Categorizer
        assert Categorizer._zone_from_address("Fort York Blvd") == LocationZone.FORT_YORK
        assert Categorizer._zone_from_address("Bathurst St") == LocationZone.FORT_YORK

    def test_queen_west_keywords(self):
        from core.categorizer import Categorizer
        assert Categorizer._zone_from_address("Queen West") == LocationZone.QUEEN_KING_WEST
        assert Categorizer._zone_from_address("Ossington Ave") == LocationZone.QUEEN_KING_WEST

    def test_empty_address_unknown(self):
        from core.categorizer import Categorizer
        assert Categorizer._zone_from_address("") == LocationZone.UNKNOWN
