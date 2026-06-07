"""
scrapers/community_sources.py — Generic scraper for Fort York-area community pages.

Handles a configurable list of community sources: The Bentway, STACKT Market,
Fort York, One Love Market, Waterfront Neighbourhood Centre, FIFA/City pages,
and City of Toronto recreation/clubs.

Each source gets a generic HTML parse followed by an LLM fallback so even
sites with non-standard markup contribute events to the digest.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from models import Event, EventSource
from scrapers.base import BaseScraper
from scrapers.ontarioplace import _parse_date

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
_CARD_SELECTORS = (
    ".event-card, .event-item, .event-listing, "
    "[class*='event-card'], [class*='event-item'], "
    "article.event, article, li.event, .card, "
    "[class*='event'], .program-item"
)


class CommunitySourcesScraper(BaseScraper):
    """Scrapes a list of community event pages defined in config.community_sources."""

    SOURCE_NAME = "community"

    def __init__(self, client, config: dict[str, Any]) -> None:
        super().__init__(client)
        cfg = config.get("community_sources", {})
        self._enabled: bool = cfg.get("enabled", True)
        self._sources: list[dict] = cfg.get("sources", [])

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        if not self._enabled or not self._sources:
            return []

        tasks = [
            self._fetch_source(src, date_from, date_to)
            for src in self._sources
            if src.get("enabled", True)
        ]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

        events: list[Event] = []
        for i, batch in enumerate(batches):
            if isinstance(batch, Exception):
                src_name = self._sources[i].get("name", str(i)) if i < len(self._sources) else str(i)
                self._log.warning("Community source '%s' failed: %s", src_name, batch)
            else:
                events.extend(batch)  # type: ignore[arg-type]

        self._log.info("CommunitySourcesScraper: %d events total", len(events))
        return events

    async def _fetch_source(
        self, src: dict, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        url: str = src["url"]
        lat: float | None = src.get("lat")
        lng: float | None = src.get("lng")
        name: str = src.get("name", "")

        resp = await self._client.get(
            url,
            headers={"User-Agent": _UA},
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        events = self._parse_page(soup, url, name, lat, lng, date_from, date_to)

        if not events:
            self._log.info("%s: HTML parse found nothing — trying LLM fallback", name)
            events = await self._llm_fallback(
                soup.get_text(" ", strip=True), url, name, lat, lng, date_from, date_to
            )

        self._log.info("%s: %d events", name, len(events))
        return events

    def _parse_page(
        self,
        soup: BeautifulSoup,
        source_url: str,
        name: str,
        lat: float | None,
        lng: float | None,
        date_from: datetime,
        date_to: datetime,
    ) -> list[Event]:
        events: list[Event] = []
        for card in soup.select(_CARD_SELECTORS):
            event = self._parse_card(card, source_url, name, lat, lng, date_from, date_to)
            if event:
                events.append(event)
        return events

    def _parse_card(
        self,
        card,
        source_url: str,
        venue_name: str,
        lat: float | None,
        lng: float | None,
        date_from: datetime,
        date_to: datetime,
    ) -> Event | None:
        try:
            title_tag = card.select_one(
                "h2, h3, h4, .title, [class*='title'], [class*='name']"
            )
            if not title_tag:
                return None
            title = title_tag.get_text(strip=True)
            if not title or len(title) < 3:
                return None

            link_tag = card.select_one("a[href]")
            href = link_tag.get("href", "") if link_tag else ""
            if href and not href.startswith("http"):
                href = urljoin(source_url, href)
            url = href or source_url

            date_tag = card.select_one(
                ".date, .event-date, time, [class*='date'], [datetime]"
            )
            event_dt: datetime | None = None
            if date_tag:
                raw = date_tag.get("datetime") or date_tag.get_text(strip=True)
                event_dt = _parse_date(raw, date_from)
            if event_dt is None:
                event_dt = _parse_date(card.get_text(" ", strip=True), date_from)
            if event_dt is None or not (date_from <= event_dt <= date_to):
                return None

            desc_tag = card.select_one(".description, .summary, p, [class*='desc']")
            description = desc_tag.get_text(strip=True) if desc_tag else ""

            price = "Free" if re.search(r"\bfree\b", card.get_text(" ", strip=True).lower()) else "Unknown"

            return Event(
                id=Event.make_id(title, event_dt),
                title=title,
                description=description,
                start_dt=event_dt,
                venue_name=venue_name,
                address="",
                city="Toronto",
                lat=lat,
                lng=lng,
                url=url,
                source=EventSource.COMMUNITY,
                price=price,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse card from %s", venue_name)
            return None

    async def _llm_fallback(
        self,
        page_text: str,
        source_url: str,
        venue_name: str,
        lat: float | None,
        lng: float | None,
        date_from: datetime,
        date_to: datetime,
    ) -> list[Event]:
        try:
            from core.llm_extractor import LLMExtractor  # noqa: PLC0415
            extractor = LLMExtractor()
            events = await extractor.extract_events(
                raw_text=page_text[:5000],
                source=EventSource.COMMUNITY,
                source_url=source_url,
                today=date_from,
            )
            for e in events:
                if not e.lat and lat is not None:
                    object.__setattr__(e, "lat", lat)
                    object.__setattr__(e, "lng", lng)
                if not e.venue_name and venue_name:
                    object.__setattr__(e, "venue_name", venue_name)
            return events
        except Exception:  # noqa: BLE001
            self._log.debug("LLM fallback failed for %s", venue_name)
            return []
