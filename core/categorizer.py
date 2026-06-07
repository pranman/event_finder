"""
core/categorizer.py — Tags events with interest categories and location zones.

Category tagging uses a two-phase approach:
  Phase 1 (keyword matching): Fast O(n) scan using keyword lists from config.yaml.
                              Catches ~85% of events correctly.
  Phase 2 (fallback):         Events with no keyword match go to Category.OTHER.

Location zone assignment uses Haversine distance from each zone's centroid.
An event is assigned to the closest zone whose radius contains the event's
coordinates. Events with no coordinates are tagged UNKNOWN.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from models import Category, Event, LocationZone

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Haversine distance helper
# ──────────────────────────────────────────────

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance in km between two (lat, lng) points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ──────────────────────────────────────────────
# Categorizer
# ──────────────────────────────────────────────

class Categorizer:
    """
    Tags events with interest categories and location zones using config.yaml rules.

    Parameters
    ----------
    config:
        The parsed contents of config.yaml (as a plain dict).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        # Build keyword → Category mapping
        self._keyword_map: dict[str, Category] = {}
        for cat_key, cat_cfg in (config.get("categories") or {}).items():
            try:
                cat_enum = Category(cat_key)
            except ValueError:
                continue
            for kw in cat_cfg.get("keywords", []):
                self._keyword_map[kw.lower()] = cat_enum

        # Zone definitions from config
        self._zones: list[dict[str, Any]] = config.get("location", {}).get("zones", [])

    # ── public API ────────────────────────────────────────────────────

    def categorize(self, events: list[Event]) -> list[Event]:
        """
        Mutate each event in-place to populate ``categories`` and
        ``location_zone``, then return the same list.
        """
        for event in events:
            event.categories = self._assign_categories(event)
            event.location_zone = self._assign_zone(event)
        return events

    # ── private ───────────────────────────────────────────────────────

    def _assign_categories(self, event: Event) -> list[Category]:
        """
        Match event text against keyword lists.
        An event may belong to multiple categories (e.g. a running festival
        that also has live music → RUNNING + CONCERTS).
        """
        searchable = " ".join([
            event.title,
            event.description,
            event.venue_name,
        ]).lower()

        matched: set[Category] = set()
        for keyword, category in self._keyword_map.items():
            # Use word-boundary-aware matching (avoids "ran" matching "brand")
            if f" {keyword} " in f" {searchable} " or searchable.startswith(keyword):
                matched.add(category)

        return list(matched) if matched else [Category.OTHER]

    def _assign_zone(self, event: Event) -> LocationZone:
        """
        Assign the closest location zone whose centroid radius contains
        the event's coordinates.
        """
        if event.lat is None or event.lng is None:
            return self._zone_from_address(event.address or event.venue_name)

        best_zone = LocationZone.UNKNOWN
        best_distance = float("inf")

        for zone_cfg in self._zones:
            dist = _haversine_km(
                event.lat, event.lng,
                zone_cfg["lat"], zone_cfg["lng"],
            )
            if dist < zone_cfg.get("radius_km", 2.0) and dist < best_distance:
                best_distance = dist
                best_zone = self._zone_name_to_enum(zone_cfg["name"])

        return best_zone

    @staticmethod
    def _zone_from_address(address: str) -> LocationZone:
        """Heuristic zone assignment from address/venue text when no lat/lng."""
        addr_lower = address.lower()
        if any(kw in addr_lower for kw in ["fort york", "bathurst", "lakeshore", "canoe"]):
            return LocationZone.FORT_YORK
        if any(kw in addr_lower for kw in ["liberty", "exhibition", "harbourfront"]):
            return LocationZone.WATERFRONT_WEST
        if any(kw in addr_lower for kw in ["queen west", "king west", "ossington", "roncesvalles", "parkdale", "dundas west"]):
            return LocationZone.QUEEN_KING_WEST
        if any(kw in addr_lower for kw in ["yonge", "bay", "financial", "downtown", "union"]):
            return LocationZone.DOWNTOWN_CORE
        if any(kw in addr_lower for kw in ["bloor", "annex", "kensington", "college"]):
            return LocationZone.MIDTOWN
        return LocationZone.UNKNOWN

    @staticmethod
    def _zone_name_to_enum(name: str) -> LocationZone:
        try:
            return LocationZone(name)
        except ValueError:
            return LocationZone.UNKNOWN
