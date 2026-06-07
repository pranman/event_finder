"""
core/aggregator.py — Merges events from all scrapers and deduplicates.

Deduplication strategy (priority order):
  1. Exact ID match  — SHA-1(normalized title + date), same across sources.
  2. Cross-source coord key — same rounded lat/lng + same date + title
     similarity ≥ 0.60 (venue confirms it's the same physical event).
  3. Title similarity ≥ 0.90 AND same date (tight threshold, last resort).

When two events are merged the one from the more authoritative source
(Luma > Eventbrite > Meetup > Songkick > BlogTO > Reddit) is kept as
canonical; the secondary URL is discarded but the richer description /
image is preferred.  ``first_seen`` is managed by StateStore, not here.

LLM extraction (Reddit / raw-text) is only run on the *weekly* path.
Pass ``llm_extractor=None`` (default) for the fast poll.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from models import Event, EventSource

logger = logging.getLogger(__name__)

# Lower value = more authoritative (kept when deduplicating)
_SOURCE_PRIORITY: dict[EventSource, int] = {
    EventSource.LUMA: 0,
    EventSource.EVENTBRITE: 1,
    EventSource.SONGKICK: 2,
    EventSource.MEETUP: 3,
    EventSource.BLOGTO: 4,
    EventSource.REDDIT: 5,
    EventSource.SAMPLE: 9,
}

# Rounded to this many decimal places for coord-based cross-source match
# ~0.5 km precision at Toronto's latitude
_COORD_PRECISION = 2


def _title_similarity(a: str, b: str) -> float:
    """Bigram Jaccard similarity in [0.0, 1.0]. Fast enough for O(n²) over ~500 events."""
    def bigrams(s: str) -> set[str]:
        s = s.lower().strip()
        return {s[i : i + 2] for i in range(len(s) - 1)}

    a_bi, b_bi = bigrams(a), bigrams(b)
    if not a_bi or not b_bi:
        return 0.0
    return len(a_bi & b_bi) / len(a_bi | b_bi)


def _coord_key(event: Event) -> str | None:
    """Return a rounded coord string for cross-source matching, or None if missing."""
    if event.lat is None or event.lng is None:
        return None
    return f"{round(event.lat, _COORD_PRECISION)},{round(event.lng, _COORD_PRECISION)}"


class Aggregator:
    """
    Merges and deduplicates events from multiple scrapers.

    Parameters
    ----------
    llm_extractor:
        Optional LLM extractor for Reddit / raw-text events.
        Pass None on the fast path to skip LLM calls entirely.
    """

    def __init__(self, llm_extractor=None) -> None:
        self._extractor = llm_extractor

    async def aggregate(self, batches: list[list[Event]]) -> list[Event]:
        """
        Merge multiple scraper result lists into a single deduplicated list.

        Parameters
        ----------
        batches:
            One list per scraper, in any order.

        Returns
        -------
        list[Event]
            Deduplicated, sorted by start_dt ascending.
        """
        all_events: list[Event] = []

        for batch in batches:
            for event in batch:
                if event.raw_text and self._extractor:
                    extracted = await self._extractor.extract_events(
                        raw_text=event.raw_text,
                        source=event.source,
                        source_url=event.url,
                    )
                    if extracted:
                        all_events.extend(extracted)
                        logger.debug(
                            "LLM extracted %d events from '%s'",
                            len(extracted), event.title[:50],
                        )
                    else:
                        all_events.append(event)
                else:
                    all_events.append(event)

        deduped = self._deduplicate(all_events)
        deduped.sort(key=lambda e: e.start_dt if e.start_dt.tzinfo else e.start_dt.replace(tzinfo=timezone.utc))
        logger.info("Aggregator: %d raw → %d after dedup", len(all_events), len(deduped))
        return deduped

    # ── private ───────────────────────────────────────────────────────

    def _deduplicate(self, events: list[Event]) -> list[Event]:
        """
        Three-pass dedup:
          1. Exact ID collision (SHA-1 title+date matches across sources)
          2. Coord-based: same venue + date + title similarity ≥ 0.60
          3. High-similarity title + same date (≥ 0.90, was 0.80 before)
        """
        # Pass 1: exact ID
        by_id: dict[str, Event] = {}
        for event in events:
            existing = by_id.get(event.id)
            if existing is None:
                by_id[event.id] = event
            else:
                by_id[event.id] = self._pick_canonical(existing, event)

        unique = list(by_id.values())

        # Pass 2 + 3: coord-key and fuzzy title
        canonical: list[Event] = []
        for candidate in unique:
            merged = False
            cand_coord = _coord_key(candidate)

            for i, existing in enumerate(canonical):
                if existing.start_dt.date() != candidate.start_dt.date():
                    continue

                sim = _title_similarity(existing.title, candidate.title)

                # Pass 2: same physical venue (within ~0.5 km) + reasonable title match.
                # Threshold is 0.75 — enough to catch "Event X" vs "Event X (June)" but
                # not short single-word titles like "Event A" vs "Event B".
                if cand_coord is not None and _coord_key(existing) == cand_coord and sim >= 0.75:
                    canonical[i] = self._pick_canonical(existing, candidate)
                    merged = True
                    break

                # Pass 3: very high title similarity (tightened from 0.80 → 0.90)
                if sim >= 0.90:
                    canonical[i] = self._pick_canonical(existing, candidate)
                    merged = True
                    break

            if not merged:
                canonical.append(candidate)

        return canonical

    @staticmethod
    def _pick_canonical(a: Event, b: Event) -> Event:
        """Return the more authoritative of two duplicate events."""
        priority_a = _SOURCE_PRIORITY.get(a.source, 99)
        priority_b = _SOURCE_PRIORITY.get(b.source, 99)
        return a if priority_a <= priority_b else b
