"""
scrapers/luma.py — Luma (lu.ma) public events API.

Luma's public discovery endpoint lets us query events by geo-coordinates
without authentication. We search with Toronto's bounding box and optionally
filter by a personal calendar API key if LUMA_API_KEY is set.

Also supports fetching events from specific Luma host profiles configured
under config['luma_hosts'] (e.g. usr-xHOhElhEccTFQVa).

API reference: https://docs.lu.ma/reference/get_public-v1-discover-get-paginated-events
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from models import Event, EventSource
from scrapers.base import BaseScraper

# Toronto bounding box (lat/lng)
_TORONTO_LAT = 43.6532
_TORONTO_LNG = -79.3832


class LumaScraper(BaseScraper):
    """Fetches public Luma events near downtown Toronto, plus watched host profiles."""

    SOURCE_NAME = "luma"

    # Luma pagination: max 50 per page
    _BASE_URL = "https://api.lu.ma/public/v1"
    _PAGE_SIZE = 50

    def __init__(self, client: httpx.AsyncClient, config: dict[str, Any] | None = None) -> None:
        super().__init__(client)
        self._hosts: list[dict] = (config or {}).get("luma_hosts", [])

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        api_key = os.getenv("LUMA_API_KEY", "").strip()
        if not api_key:
            self._log.info("LUMA_API_KEY not set — skipping Luma")
            return []

        headers: dict[str, str] = {
            "accept": "application/json",
            "x-luma-api-key": api_key,
        }

        events: list[Event] = []
        pagination_cursor: str | None = None

        while True:
            params: dict[str, Any] = {
                "pagination_limit": self._PAGE_SIZE,
                "after": date_from.isoformat(),
                "before": date_to.isoformat(),
                "latitude": _TORONTO_LAT,
                "longitude": _TORONTO_LNG,
                "radius": 20,
            }
            if pagination_cursor:
                params["pagination_cursor"] = pagination_cursor

            resp = await self._client.get(
                f"{self._BASE_URL}/calendar/list-events",
                params=params,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for entry in data.get("entries", []):
                event = entry.get("event", {})
                parsed = self._parse_event(event)
                if parsed:
                    events.append(parsed)

            next_cursor = data.get("next_cursor")
            if not next_cursor or not data.get("has_more", False):
                break
            pagination_cursor = next_cursor

        self._log.info("Luma geo: fetched %d events", len(events))

        host_events = await self._fetch_hosts(date_from, date_to, headers)
        self._log.info("Luma hosts: fetched %d events", len(host_events))
        events.extend(host_events)

        return events

    async def _fetch_hosts(
        self,
        date_from: datetime,
        date_to: datetime,
        headers: dict[str, str],
    ) -> list[Event]:
        events: list[Event] = []
        for host in self._hosts:
            host_id = host.get("id", "")
            if not host_id:
                continue
            try:
                batch = await self._fetch_host(host_id, date_from, date_to, headers)
                events.extend(batch)
            except Exception:  # noqa: BLE001
                self._log.warning("Could not fetch Luma host %s — skipping", host_id)
        return events

    async def _fetch_host(
        self,
        host_id: str,
        date_from: datetime,
        date_to: datetime,
        headers: dict[str, str],
    ) -> list[Event]:
        # Luma exposes hosted events via the calendar endpoint when the host ID
        # is passed as calendar_api_id; fall back gracefully if unsupported.
        params: dict[str, Any] = {
            "pagination_limit": self._PAGE_SIZE,
            "after": date_from.isoformat(),
            "before": date_to.isoformat(),
        }
        # Try user-scoped endpoint first
        for endpoint in (
            f"{self._BASE_URL}/user/get-events",
            f"{self._BASE_URL}/calendar/get-events",
        ):
            p = {**params, "user_api_id" if "user" in endpoint else "calendar_api_id": host_id}
            try:
                resp = await self._client.get(endpoint, params=p, headers=headers, timeout=15)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                events: list[Event] = []
                for entry in data.get("entries", []):
                    raw = entry.get("event", entry)
                    parsed = self._parse_event(raw)
                    if parsed:
                        events.append(parsed)
                self._log.info("Luma host %s → %d events", host_id, len(events))
                return events
            except httpx.HTTPStatusError:
                continue
        self._log.debug("Luma host %s: no supported endpoint found", host_id)
        return []

    # ── private ───────────────────────────────────────────────────────

    def _parse_event(self, raw: dict[str, Any]) -> Event | None:
        """Convert a raw Luma API event dict into a canonical Event."""
        try:
            title = raw.get("name", "").strip()
            url = f"https://lu.ma/{raw.get('url', '')}"
            start_str = raw.get("start_at") or raw.get("start_time")
            if not title or not start_str:
                return None

            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end_str = raw.get("end_at") or raw.get("end_time")
            end_dt = (
                datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end_str
                else None
            )

            geo = raw.get("geo_address_json") or {}
            return Event(
                id=Event.make_id(title, start_dt),
                title=title,
                description=raw.get("description", ""),
                start_dt=start_dt,
                end_dt=end_dt,
                venue_name=raw.get("location", {}).get("name", ""),
                address=geo.get("full_address", ""),
                city=geo.get("city", "Toronto"),
                lat=geo.get("latitude"),
                lng=geo.get("longitude"),
                url=url,
                source=EventSource.LUMA,
                price=self._safe_price(raw.get("ticket_info", {}).get("price")),
                image_url=raw.get("cover_url"),
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse Luma event: %s", raw.get("name"))
            return None
