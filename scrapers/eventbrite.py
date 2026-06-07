"""
scrapers/eventbrite.py — Eventbrite public search API.

Uses the Eventbrite v3 REST API to search for upcoming events in Toronto.
Requires a free API key (EVENTBRITE_API_KEY in .env).

API reference: https://www.eventbrite.com/platform/api#/reference/event/search
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from models import Event, EventSource
from scrapers.base import BaseScraper


class EventbriteScraper(BaseScraper):
    """Fetches Eventbrite events in Toronto."""

    SOURCE_NAME = "eventbrite"

    _BASE_URL = "https://www.eventbriteapi.com/v3"
    _PAGE_SIZE = 50

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        api_key = os.getenv("EVENTBRITE_API_KEY", "").strip()
        if not api_key:
            self._log.warning("EVENTBRITE_API_KEY not set — skipping Eventbrite")
            return []

        headers = {"Authorization": f"Bearer {api_key}"}
        events: list[Event] = []
        page = 1

        while True:
            params = {
                "location.address": "Toronto, ON",
                "location.within": "20km",
                "start_date.range_start": date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "start_date.range_end": date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expand": "venue,ticket_availability",
                "page": page,
                "page_size": self._PAGE_SIZE,
                "sort_by": "date",
                "status": "live",
            }

            resp = await self._client.get(
                f"{self._BASE_URL}/events/search/",
                params=params,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for raw in data.get("events", []):
                parsed = self._parse_event(raw)
                if parsed:
                    events.append(parsed)

            pagination = data.get("pagination", {})
            if page >= pagination.get("page_count", 1):
                break
            page += 1

        self._log.info("Eventbrite: fetched %d events", len(events))
        return events

    def _parse_event(self, raw: dict[str, Any]) -> Event | None:
        try:
            title = (raw.get("name") or {}).get("text", "").strip()
            if not title:
                return None

            start_str = (raw.get("start") or {}).get("utc", "")
            if not start_str:
                return None

            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end_str = (raw.get("end") or {}).get("utc", "")
            end_dt = (
                datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end_str
                else None
            )

            venue = raw.get("venue") or {}
            address = venue.get("address") or {}
            full_address = ", ".join(
                filter(None, [
                    address.get("address_1", ""),
                    address.get("city", "Toronto"),
                    address.get("region", "ON"),
                ])
            )

            ticket_info = raw.get("ticket_availability") or {}
            price = "Free" if ticket_info.get("is_free") else self._safe_price(
                (ticket_info.get("minimum_ticket_price") or {}).get("display")
            )

            url = raw.get("url", "")
            logo = (raw.get("logo") or {}).get("url")

            return Event(
                id=Event.make_id(title, start_dt),
                title=title,
                description=(raw.get("description") or {}).get("text", ""),
                start_dt=start_dt,
                end_dt=end_dt,
                venue_name=venue.get("name", ""),
                address=full_address,
                city=address.get("city", "Toronto"),
                lat=venue.get("latitude"),
                lng=venue.get("longitude"),
                url=url,
                source=EventSource.EVENTBRITE,
                price=price,
                image_url=logo,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse Eventbrite event: %s", raw.get("name"))
            return None
