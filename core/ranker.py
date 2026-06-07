"""
core/ranker.py — Scores events by relevance to the user's preferences.

Relevance score is a float in [0.0, 1.0] built from weighted signals:

  Signal                          Weight
  ─────────────────────────────── ──────
  Interest category match         0.40
  Proximity to home (Fort York)   0.30
  Is free / low price             0.10
  This weekend bonus              0.10
  Has image / rich info           0.05
  Has specific venue (not TBD)    0.05

Top picks = events with score ≥ 0.55.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from models import Category, Event, LocationZone

logger = logging.getLogger(__name__)

# Weights must sum to 1.0
_W_CATEGORY = 0.40
_W_PROXIMITY = 0.30
_W_PRICE = 0.10
_W_WEEKEND = 0.10
_W_RICH_INFO = 0.05
_W_VENUE = 0.05

# Distance at which proximity score → 0
_MAX_PROXIMITY_KM = 15.0


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class Ranker:
    """
    Scores and sorts events by personal relevance.

    Parameters
    ----------
    config:
        Parsed config.yaml dict — used for home coordinates.
    preferred_categories:
        User's preferred categories. Events matching these get the full
        category weight; others get a partial score.
    """

    def __init__(
        self,
        config: dict[str, Any],
        preferred_categories: list[Category] | None = None,
    ) -> None:
        home = config.get("user", {}).get("home", {})
        self._home_lat: float = home.get("lat", 43.6385)
        self._home_lng: float = home.get("lng", -79.4028)
        self._preferred = set(preferred_categories or [
            Category.TECH, Category.CONCERTS, Category.RUNNING
        ])

    def rank(self, events: list[Event], now: datetime | None = None) -> list[Event]:
        """
        Score each event and return the list sorted by score descending.

        Parameters
        ----------
        events:
            Already categorized events (categories field must be populated).
        now:
            Reference datetime (defaults to current UTC time).
        """
        now = now or datetime.now()
        for event in events:
            event.relevance_score = round(self._score(event, now), 4)

        events.sort(key=lambda e: e.relevance_score, reverse=True)
        logger.info("Ranker: top score=%.2f, bottom=%.2f",
                    events[0].relevance_score if events else 0,
                    events[-1].relevance_score if events else 0)
        return events

    @property
    def top_picks_threshold(self) -> float:
        return 0.55

    # ── private ───────────────────────────────────────────────────────

    def _score(self, event: Event, now: datetime) -> float:
        return (
            _W_CATEGORY  * self._category_score(event)
            + _W_PROXIMITY * self._proximity_score(event)
            + _W_PRICE     * self._price_score(event)
            + _W_WEEKEND   * self._weekend_score(event, now)
            + _W_RICH_INFO * self._rich_info_score(event)
            + _W_VENUE     * self._venue_score(event)
        )

    def _category_score(self, event: Event) -> float:
        """1.0 if any preferred category matches, 0.3 otherwise."""
        if any(c in self._preferred for c in event.categories):
            return 1.0
        return 0.3  # give some credit to OTHER nearby events

    def _proximity_score(self, event: Event) -> float:
        """Linear decay from 1.0 at home → 0.0 at MAX_PROXIMITY_KM away."""
        if event.lat is None or event.lng is None:
            # Partial credit if zone is close to home
            if event.location_zone in {LocationZone.FORT_YORK, LocationZone.WATERFRONT_WEST, LocationZone.QUEEN_KING_WEST}:
                return 0.6
            return 0.3
        dist = _haversine_km(self._home_lat, self._home_lng, event.lat, event.lng)
        return max(0.0, 1.0 - dist / _MAX_PROXIMITY_KM)

    @staticmethod
    def _price_score(event: Event) -> float:
        """Free events score 1.0; paid events 0.5; unknown 0.3."""
        price = event.price.lower()
        if "free" in price or price in {"0", ""}:
            return 1.0
        if price == "unknown":
            return 0.3
        return 0.5

    @staticmethod
    def _weekend_score(event: Event, now: datetime) -> float:
        """
        Events on this coming Friday/Saturday/Sunday get a bonus.
        Events further out get less benefit.
        """
        days_away = (event.start_dt.date() - now.date()).days
        if days_away < 0:
            return 0.0
        weekday = event.start_dt.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
        is_weekend = weekday in {4, 5, 6}
        if is_weekend and days_away <= 3:
            return 1.0
        if is_weekend:
            return 0.7
        return 0.4

    @staticmethod
    def _rich_info_score(event: Event) -> float:
        """Reward events with images and descriptions."""
        score = 0.0
        if event.image_url:
            score += 0.5
        if len(event.description) > 50:
            score += 0.5
        return score

    @staticmethod
    def _venue_score(event: Event) -> float:
        """Reward events with a specific named venue."""
        return 1.0 if event.venue_name.strip() else 0.0
