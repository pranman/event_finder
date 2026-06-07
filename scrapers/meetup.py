"""
scrapers/meetup.py — Meetup.com GraphQL API.

Queries meetup.com for upcoming events near Toronto.
Also fetches events from any groups pinned in config['meetup_groups'].
Requires a free OAuth token (MEETUP_API_KEY in .env).

GraphQL endpoint: https://api.meetup.com/gql
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from models import Event, EventSource
from scrapers.base import BaseScraper

_GRAPHQL_URL = "https://api.meetup.com/gql"

_EVENTS_QUERY = """
query UpcomingEvents($lat: Float!, $lon: Float!, $radius: Float!, $after: String) {
  keywordSearch(
    filter: {
      lat: $lat
      lon: $lon
      radius: $radius
      startDateRange: $after
      source: EVENTS
    }
    input: { first: 50 }
  ) {
    edges {
      node {
        result {
          ... on Event {
            id
            title
            description
            dateTime
            endTime
            eventUrl
            isOnline
            going
            maxTickets
            rsvpSettings { rsvpOpenTime rsvpCloseTime }
            venue {
              name
              address
              city
              lat
              lng
            }
            group {
              name
              urlname
            }
          }
        }
      }
    }
  }
}
"""

_GROUP_EVENTS_QUERY = """
query GroupEvents($urlname: String!) {
  groupByUrlname(urlname: $urlname) {
    upcomingEvents(input: { first: 20 }) {
      edges {
        node {
          id
          title
          description
          dateTime
          endTime
          eventUrl
          isOnline
          venue {
            name
            address
            city
            lat
            lng
          }
        }
      }
    }
  }
}
"""


class MeetupScraper(BaseScraper):
    """Fetches Meetup.com events near downtown Toronto plus pinned groups."""

    SOURCE_NAME = "meetup"

    def __init__(self, client, config: dict[str, Any] | None = None) -> None:
        super().__init__(client)
        self._pinned_groups: list[dict] = (config or {}).get("meetup_groups", [])

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        api_key = os.getenv("MEETUP_API_KEY", "").strip()
        if not api_key:
            self._log.warning("MEETUP_API_KEY not set — skipping Meetup")
            return []

        tasks: list = [self._fetch_geo(api_key, date_from, date_to)]
        for group in self._pinned_groups:
            tasks.append(self._fetch_group(api_key, group["urlname"], date_from, date_to))

        batches = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        events: list[Event] = []
        for batch in batches:
            if isinstance(batch, Exception):
                self._log.warning("Meetup batch failed: %s", batch)
                continue
            for e in batch:  # type: ignore[union-attr]
                if e.id not in seen:
                    seen.add(e.id)
                    events.append(e)

        self._log.info("Meetup: fetched %d events (%d pinned groups)", len(events), len(self._pinned_groups))
        return events

    async def _fetch_geo(
        self, api_key: str, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        variables = {
            "lat": 43.6532,
            "lon": -79.3832,
            "radius": 20.0,
            "after": date_from.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        resp = await self._client.post(
            _GRAPHQL_URL,
            json={"query": _EVENTS_QUERY, "variables": variables},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        edges = (
            resp.json().get("data", {})
            .get("keywordSearch", {})
            .get("edges", [])
        )
        events: list[Event] = []
        for edge in edges:
            raw = (edge.get("node") or {}).get("result") or {}
            parsed = self._parse_event(raw, date_to)
            if parsed:
                events.append(parsed)
        return events

    async def _fetch_group(
        self, api_key: str, urlname: str, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        resp = await self._client.post(
            _GRAPHQL_URL,
            json={"query": _GROUP_EVENTS_QUERY, "variables": {"urlname": urlname}},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        edges = (
            resp.json().get("data", {})
            .get("groupByUrlname", {})
            .get("upcomingEvents", {})
            .get("edges", [])
        )
        events: list[Event] = []
        for edge in edges:
            raw = edge.get("node") or {}
            parsed = self._parse_event(raw, date_to)
            if parsed and parsed.start_dt >= date_from:
                events.append(parsed)
        return events

    def _parse_event(self, raw: dict[str, Any], date_to: datetime) -> Event | None:
        try:
            title = raw.get("title", "").strip()
            start_str = raw.get("dateTime", "")
            if not title or not start_str:
                return None

            start_dt = datetime.fromisoformat(start_str)

            # Filter events outside our date window
            if start_dt > date_to:
                return None

            # Skip online-only events
            if raw.get("isOnline"):
                return None

            end_str = raw.get("endTime", "")
            end_dt = datetime.fromisoformat(end_str) if end_str else None

            venue = raw.get("venue") or {}
            return Event(
                id=Event.make_id(title, start_dt),
                title=title,
                description=raw.get("description", ""),
                start_dt=start_dt,
                end_dt=end_dt,
                venue_name=venue.get("name", ""),
                address=venue.get("address", ""),
                city=venue.get("city", "Toronto"),
                lat=venue.get("lat"),
                lng=venue.get("lng"),
                url=raw.get("eventUrl", ""),
                source=EventSource.MEETUP,
                price="Free",  # Meetup is usually free
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse Meetup event: %s", raw.get("title"))
            return None
