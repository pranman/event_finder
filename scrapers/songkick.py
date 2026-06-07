"""
scrapers/songkick.py — Songkick concert calendar API.

Fetches upcoming concerts and live music events in the Toronto metro area.
Toronto metro area ID on Songkick: 6070.

API reference: https://www.songkick.com/developer/upcoming-events-for-metro-area
Requires: SONGKICK_API_KEY in .env (free at songkick.com/developer)
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from models import Event, EventSource
from scrapers.base import BaseScraper

# Songkick metro area ID for Toronto
_TORONTO_METRO_ID = 6070


class SongkickScraper(BaseScraper):
    """Fetches concerts and live music events from Songkick for Toronto."""

    SOURCE_NAME = "songkick"

    _BASE_URL = "https://api.songkick.com/api/3.0"

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        api_key = os.getenv("SONGKICK_API_KEY", "").strip()
        if not api_key:
            self._log.warning("SONGKICK_API_KEY not set — skipping Songkick")
            return []

        events: list[Event] = []
        page = 1

        while True:
            params = {
                "apikey": api_key,
                "min_date": date_from.strftime("%Y-%m-%d"),
                "max_date": date_to.strftime("%Y-%m-%d"),
                "page": page,
                "per_page": 50,
            }

            resp = await self._client.get(
                f"{self._BASE_URL}/metro_areas/{_TORONTO_METRO_ID}/calendar.json",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            results = data.get("resultsPage", {})
            for raw in results.get("results", {}).get("event", []):
                parsed = self._parse_event(raw)
                if parsed:
                    events.append(parsed)

            total_entries = results.get("totalEntries", 0)
            entries_per_page = results.get("perPage", 50)
            if page * entries_per_page >= total_entries:
                break
            page += 1

        self._log.info("Songkick: fetched %d events", len(events))
        return events

    def _parse_event(self, raw: dict[str, Any]) -> Event | None:
        try:
            # Build title: "Artist1, Artist2 at Venue"
            performances = raw.get("performance", [])
            artists = ", ".join(
                p.get("artist", {}).get("displayName", "")
                for p in performances[:3]
                if p.get("billing") == "headline"
            ) or ", ".join(
                p.get("artist", {}).get("displayName", "")
                for p in performances[:2]
            )
            venue_info = raw.get("venue") or {}
            venue_name = venue_info.get("displayName", "")
            title = f"{artists} at {venue_name}" if artists else venue_name
            if not title.strip():
                return None

            start = raw.get("start") or {}
            date_str = start.get("date", "")
            time_str = start.get("time", "")
            if not date_str:
                return None

            dt_str = f"{date_str}T{time_str}" if time_str else f"{date_str}T19:00:00"
            start_dt = datetime.fromisoformat(dt_str)

            location = venue_info.get("metroArea", {})
            lat = venue_info.get("lat")
            lng = venue_info.get("lng")

            return Event(
                id=Event.make_id(title, start_dt),
                title=title,
                description=f"Live concert at {venue_name}",
                start_dt=start_dt,
                venue_name=venue_name,
                address=venue_info.get("street", ""),
                city=location.get("displayName", "Toronto"),
                lat=float(lat) if lat else None,
                lng=float(lng) if lng else None,
                url=raw.get("uri", ""),
                source=EventSource.SONGKICK,
                price="Unknown",
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse Songkick event: %s", raw.get("id"))
            return None
