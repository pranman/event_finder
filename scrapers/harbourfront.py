"""
scrapers/harbourfront.py — Harbourfront Centre event scraper.

Harbourfront Centre (235 Queens Quay W) is ~2 km from Fort York and hosts
free concerts, outdoor movies, yoga, festivals, and art events year-round.
Their "What's On" page has structured event cards.

No API key required.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from models import Event, EventSource
from scrapers.base import BaseScraper
from scrapers.ontarioplace import _parse_date   # shared date parser

_BASE = "https://harbourfrontcentre.com"
_LAT = 43.6387
_LNG = -79.3816
_ADDRESS = "235 Queens Quay W, Toronto, ON M5J 2G8"
_VENUE = "Harbourfront Centre"


class HarbourfrontScraper(BaseScraper):
    """Scrapes upcoming events from harbourfrontcentre.com/whats-on/."""

    SOURCE_NAME = "harbourfront"

    def __init__(self, client, config: dict[str, Any]) -> None:
        super().__init__(client)
        hf_cfg = config.get("harbourfront", {})
        self._url: str = hf_cfg.get("url", f"{_BASE}/whats-on/")
        self._enabled: bool = hf_cfg.get("enabled", True)

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        if not self._enabled:
            return []

        resp = await self._client.get(
            self._url,
            headers={"User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )},
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        events = self._parse_events(soup, date_from, date_to)

        if not events:
            self._log.info("Harbourfront: HTML parse empty — trying LLM fallback")
            events = await self._llm_fallback(
                soup.get_text(" ", strip=True), date_from, date_to
            )

        self._log.info("Harbourfront: %d events", len(events))
        return events

    def _parse_events(
        self, soup: BeautifulSoup, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        events: list[Event] = []

        cards = soup.select(
            ".event-card, .event-item, .event-listing, "
            "[class*='event-card'], [class*='event-item'], "
            "article.event, article, .card, li.event"
        )

        for card in cards:
            event = self._parse_card(card, date_from, date_to)
            if event:
                events.append(event)

        return events

    def _parse_card(
        self, card, date_from: datetime, date_to: datetime
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
                href = f"{_BASE}{href}"
            url = href or self._url

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

            card_text_lower = card.get_text(" ", strip=True).lower()
            price = "Free" if re.search(r"\bfree\b", card_text_lower) else "Unknown"

            return Event(
                id=Event.make_id(title, event_dt),
                title=title,
                description=description,
                start_dt=event_dt,
                venue_name=_VENUE,
                address=_ADDRESS,
                city="Toronto",
                lat=_LAT,
                lng=_LNG,
                url=url,
                source=EventSource.LUMA,   # closest semantic fit; no dedicated source yet
                price=price,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse Harbourfront card")
            return None

    async def _llm_fallback(
        self, page_text: str, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        try:
            from core.llm_extractor import LLMExtractor  # noqa: PLC0415
            extractor = LLMExtractor()
            events = await extractor.extract_events(
                raw_text=page_text[:5000],
                source=EventSource.LUMA,
                source_url=self._url,
                today=date_from,
            )
            for e in events:
                if not e.lat:
                    object.__setattr__(e, "lat", _LAT)
                    object.__setattr__(e, "lng", _LNG)
                if not e.venue_name:
                    object.__setattr__(e, "venue_name", _VENUE)
            return events
        except Exception:  # noqa: BLE001
            self._log.debug("Harbourfront LLM fallback failed")
            return []
