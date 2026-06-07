"""
events_finder — shared Pydantic data models.

All scrapers produce Event objects; downstream logic (aggregator,
categorizer, ranker, email renderer) consumes them.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field


# ──────────────────────────────────────────────
# Enumerations
# ──────────────────────────────────────────────

class Category(str, Enum):
    TECH = "tech"
    CONCERTS = "concerts"
    RUNNING = "running"
    OTHER = "other"


class LocationZone(str, Enum):
    FORT_YORK = "Fort York"
    WATERFRONT_WEST = "Waterfront West"
    QUEEN_KING_WEST = "Queen/King West"
    DOWNTOWN_CORE = "Downtown Core"
    MIDTOWN = "Midtown"
    UNKNOWN = "Unknown"


class EventSource(str, Enum):
    LUMA = "luma"
    EVENTBRITE = "eventbrite"
    MEETUP = "meetup"
    SONGKICK = "songkick"
    BLOGTO = "blogto"
    REDDIT = "reddit"
    INSTAGRAM = "instagram"
    ONTARIOPLACE = "ontarioplace"
    COMMUNITY = "community"
    SAMPLE = "sample"   # used in tests / dry-runs


# ──────────────────────────────────────────────
# Core Event model
# ──────────────────────────────────────────────

class Event(BaseModel):
    """
    Canonical representation of a Toronto event, regardless of source.

    The `id` field is a stable, deterministic hash derived from the
    event title and start date so that the same real-world event
    fetched from two different scrapers can be deduplicated.
    """

    # Identification
    id: str = Field(
        description="Deterministic SHA-1 hash of (title + ISO date). "
                    "Used for deduplication across sources.",
    )
    title: str
    description: str = ""

    # Timing
    start_dt: datetime
    end_dt: Optional[datetime] = None

    # Location
    venue_name: str = ""
    address: str = ""
    city: str = "Toronto"
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_zone: LocationZone = LocationZone.UNKNOWN

    # Meta
    url: str
    source: EventSource
    price: str = "Unknown"          # "Free", "$20", "TBD", etc.
    image_url: Optional[str] = None
    raw_text: Optional[str] = None  # original text for LLM-extracted events

    # Classification (populated by core/categorizer.py)
    categories: list[Category] = Field(default_factory=list)

    # Scoring (populated by core/ranker.py)
    relevance_score: float = 0.0

    # ── helpers ──────────────────────────────

    @computed_field  # type: ignore[prop-decorator]
    @property
    def day_label(self) -> str:
        """Human-friendly day string, e.g. 'Saturday Jun 14'."""
        return self.start_dt.strftime("%A %b %-d")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def time_label(self) -> str:
        """Human-friendly time string, e.g. '7:00 PM'."""
        return self.start_dt.strftime("%-I:%M %p")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_free(self) -> bool:
        return self.price.strip().lower() in {"free", "0", "$0", ""}

    def model_post_init(self, __context: object) -> None:  # noqa: D401
        """Strip whitespace from key string fields after construction."""
        self.title = self.title.strip()
        self.description = self.description.strip()
        self.venue_name = self.venue_name.strip()
        self.address = self.address.strip()

    # ── factory ──────────────────────────────

    @staticmethod
    def make_id(title: str, start_dt: datetime) -> str:
        """
        Build a stable event ID from title + date.

        Only the *date* portion of start_dt is used so that minor
        schedule adjustments (e.g. 7 PM → 7:30 PM) don't create
        duplicates.
        """
        key = f"{title.lower().strip()}|{start_dt.date().isoformat()}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]


# ──────────────────────────────────────────────
# Digest summary model (used by MCP tools)
# ──────────────────────────────────────────────

class DigestSummary(BaseModel):
    """High-level summary returned by the MCP preview_digest tool."""

    generated_at: datetime
    date_range_start: datetime
    date_range_end: datetime
    total_events: int
    events_by_category: dict[str, int]
    events_by_zone: dict[str, int]
    top_picks: list[Event]
    all_events: list[Event]
