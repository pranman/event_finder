"""
scrapers/ontarioplace.py — Ontario Place event scraper.

Scrapes the Ontario Place events calendar, summer series, and yoga/fitness
program pages. Ontario Place is ~1.5 km from Fort York so everything here
gets a natural proximity boost in the ranker.

No API key required.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from models import Event, EventSource
from scrapers.base import BaseScraper

# Ontario Place lat/lng (Trillium Park / West Island)
_LAT = 43.6289
_LNG = -79.4170
_ADDRESS = "955 Lake Shore Blvd W, Toronto, ON M6K 3B9"


class OntarioPlaceScraper(BaseScraper):
    """
    Scrapes Ontario Place event URLs from config['ontario_place']['urls'].
    Falls back to LLM extraction when HTML structure doesn't match.
    """

    SOURCE_NAME = "ontarioplace"

    def __init__(self, client, config: dict[str, Any]) -> None:
        super().__init__(client)
        op_cfg = config.get("ontario_place", {})
        self._urls: list[str] = op_cfg.get("urls", [])
        self._enabled: bool = op_cfg.get("enabled", True)

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        if not self._enabled or not self._urls:
            return []

        events: list[Event] = []
        for url in self._urls:
            try:
                batch = await self._fetch_url(url, date_from, date_to)
                self._log.info("Ontario Place %s → %d events", url.split("/")[-2], len(batch))
                events.extend(batch)
            except Exception:  # noqa: BLE001
                self._log.exception("Failed scraping Ontario Place URL: %s", url)

        # Deduplicate by ID within this scraper
        seen: set[str] = set()
        unique: list[Event] = []
        for e in events:
            if e.id not in seen:
                seen.add(e.id)
                unique.append(e)
        return unique

    async def _fetch_url(
        self, url: str, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        resp = await self._client.get(
            url,
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
        events = self._parse_events(soup, url, date_from, date_to)

        if not events:
            self._log.info("Ontario Place HTML parse empty — trying LLM fallback for %s", url)
            events = await self._llm_fallback(
                soup.get_text(" ", strip=True), url, date_from, date_to
            )

        return events

    def _parse_events(
        self,
        soup: BeautifulSoup,
        source_url: str,
        date_from: datetime,
        date_to: datetime,
    ) -> list[Event]:
        events: list[Event] = []

        # Ontario Place uses a variety of card patterns — try them all
        cards = soup.select(
            ".event-card, .event-item, article.event, "
            "[class*='event-card'], [class*='event-item'], "
            ".program-card, [class*='program-card'], "
            "article, .card"
        )

        for card in cards:
            event = self._parse_card(card, source_url, date_from, date_to)
            if event:
                events.append(event)

        return events

    def _parse_card(
        self,
        card,
        source_url: str,
        date_from: datetime,
        date_to: datetime,
    ) -> Event | None:
        try:
            title_tag = card.select_one(
                "h2, h3, h4, h5, .title, [class*='title'], [class*='name'], .heading"
            )
            if not title_tag:
                return None
            title = title_tag.get_text(strip=True)
            if not title or len(title) < 3:
                return None

            # Link
            link_tag = card.select_one("a[href]")
            href = link_tag.get("href", "") if link_tag else ""
            if href and not href.startswith("http"):
                href = f"https://ontarioplace.com{href}"
            url = href or source_url

            # Date — look for date elements or text with month names
            date_tag = card.select_one(
                ".date, .event-date, time, [class*='date'], [datetime]"
            )
            event_dt: datetime | None = None
            if date_tag:
                raw_date = date_tag.get("datetime") or date_tag.get_text(strip=True)
                event_dt = _parse_date(raw_date, date_from)

            if event_dt is None:
                # Try full card text for a date string
                card_text = card.get_text(" ", strip=True)
                event_dt = _parse_date(card_text, date_from)

            if event_dt is None or not (date_from <= event_dt <= date_to):
                return None

            # Description
            desc_tag = card.select_one(
                ".description, .summary, p, [class*='desc'], [class*='summary']"
            )
            description = desc_tag.get_text(strip=True) if desc_tag else ""

            price_tag = card.select_one("[class*='price'], [class*='cost'], [class*='ticket']")
            price_text = price_tag.get_text(strip=True).lower() if price_tag else ""
            price = "Free" if "free" in price_text else ("Unknown" if not price_text else price_text)

            return Event(
                id=Event.make_id(title, event_dt),
                title=title,
                description=description,
                start_dt=event_dt,
                venue_name="Ontario Place",
                address=_ADDRESS,
                city="Toronto",
                lat=_LAT,
                lng=_LNG,
                url=url,
                source=EventSource.ONTARIOPLACE,
                price=price,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse Ontario Place card")
            return None

    async def _llm_fallback(
        self,
        page_text: str,
        source_url: str,
        date_from: datetime,
        date_to: datetime,
    ) -> list[Event]:
        try:
            from core.llm_extractor import LLMExtractor  # noqa: PLC0415
            extractor = LLMExtractor()
            events = await extractor.extract_events(
                raw_text=page_text[:5000],
                source=EventSource.ONTARIOPLACE,
                source_url=source_url,
                today=date_from,
            )
            # Stamp location on LLM-extracted events that lack it
            for e in events:
                if not e.lat:
                    object.__setattr__(e, "lat", _LAT)
                    object.__setattr__(e, "lng", _LNG)
                if not e.address:
                    object.__setattr__(e, "address", _ADDRESS)
                if not e.venue_name:
                    object.__setattr__(e, "venue_name", "Ontario Place")
            return events
        except Exception:  # noqa: BLE001
            self._log.debug("Ontario Place LLM fallback failed")
            return []


# ── Date parsing helpers ──────────────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}

_DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _parse_date(text: str, reference: datetime) -> datetime | None:
    """Best-effort date extraction from arbitrary text snippets."""
    if not text:
        return None
    text = text.strip()

    # ISO date: 2025-06-15 or datetime attr
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            hour=9, tzinfo=reference.tzinfo)
        except ValueError:
            pass

    # "June 15", "Jun 15, 2025", "15 June"
    m = re.search(
        r"(?:(\d{1,2})\s+)?([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:[,\s]+(\d{4}))?",
        text,
    )
    if m:
        month_str = m.group(2).lower()
        if month_str in _MONTHS:
            month = _MONTHS[month_str]
            day = int(m.group(3) if m.group(3) else (m.group(1) or 1))
            year = int(m.group(4)) if m.group(4) else reference.year
            try:
                dt = datetime(year, month, day, hour=9, tzinfo=reference.tzinfo)
                # Bump year if date already passed
                if dt < reference:
                    dt = dt.replace(year=dt.year + 1)
                return dt
            except ValueError:
                pass

    # Weekday references: "Every Saturday", "Sundays"
    text_lower = text.lower()
    for day_name, weekday in _DAY_MAP.items():
        if re.search(rf"\b{day_name}s?\b", text_lower):
            dt = reference.replace(hour=9, minute=0, second=0, microsecond=0)
            days_ahead = (weekday - dt.weekday()) % 7 or 7
            return dt + timedelta(days=days_ahead)

    return None
