"""
scrapers/runclubs.py — runclubs.ca/toronto scraper.

runclubs.ca lists Toronto running clubs with recurring weekly schedules
rather than one-off events. This scraper fetches the directory, parses
each club card, then generates Event objects for any occurrences that
fall within the requested date window.

No API key required.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import NamedTuple

from bs4 import BeautifulSoup, Tag

from models import Event, EventSource
from scrapers.base import BaseScraper

_URL = "https://runclubs.ca/toronto"

_DAY_MAP: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


class _ClubInfo(NamedTuple):
    name: str
    url: str
    address: str
    schedule_text: str
    image_url: str | None


class RunClubsCaScraper(BaseScraper):
    """Scrapes recurring Toronto run club events from runclubs.ca/toronto."""

    SOURCE_NAME = "runclubs"

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        resp = await self._client.get(
            _URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        clubs = self._parse_clubs(soup)

        events: list[Event] = []
        for club in clubs:
            events.extend(self._club_to_events(club, date_from, date_to))

        # Fallback: if HTML parsing found nothing, try LLM extraction on page text
        if not events:
            self._log.info("runclubs.ca: HTML parse found nothing, trying text extraction")
            events = await self._llm_fallback(soup.get_text(" ", strip=True), date_from, date_to)

        self._log.info("runclubs.ca: produced %d events", len(events))
        return events

    # ── HTML parsing ──────────────────────────────────────────────────

    def _parse_clubs(self, soup: BeautifulSoup) -> list[_ClubInfo]:
        clubs: list[_ClubInfo] = []

        # Try multiple common card selectors — adapt as site evolves
        cards = soup.select(
            ".club-card, .club-item, article.club, "
            ".run-club, div[class*='club'], li[class*='club']"
        )
        if not cards:
            # Broader fallback: any article or card-looking element with a heading
            cards = soup.select("article, .card, .listing-item")

        for card in cards:
            info = self._parse_card(card)
            if info:
                clubs.append(info)

        return clubs

    def _parse_card(self, tag: Tag) -> _ClubInfo | None:
        try:
            # Name
            name_tag = tag.select_one("h2, h3, h4, .club-name, .title, [class*='name']")
            if not name_tag:
                return None
            name = name_tag.get_text(strip=True)
            if not name:
                return None

            # URL
            link_tag = tag.select_one("a[href]")
            href = link_tag.get("href", "") if link_tag else ""
            if href and not href.startswith("http"):
                href = f"https://runclubs.ca{href}"
            url = href or _URL

            # Address / location
            addr_tag = tag.select_one(
                ".address, .location, .venue, [class*='address'], [class*='location']"
            )
            address = addr_tag.get_text(strip=True) if addr_tag else "Toronto, ON"

            # Schedule text — look for day/time mentions
            sched_tag = tag.select_one(
                ".schedule, .time, .when, [class*='schedule'], [class*='time']"
            )
            if sched_tag:
                schedule_text = sched_tag.get_text(" ", strip=True)
            else:
                # Fall back to full card text — the schedule is usually in there
                schedule_text = tag.get_text(" ", strip=True)

            # Image
            img_tag = tag.select_one("img[src]")
            image_url = img_tag.get("src") if img_tag else None

            return _ClubInfo(
                name=name,
                url=url,
                address=address,
                schedule_text=schedule_text,
                image_url=str(image_url) if image_url else None,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse runclubs.ca card")
            return None

    # ── Schedule → Event objects ──────────────────────────────────────

    def _club_to_events(
        self, club: _ClubInfo, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        occurrences = _parse_schedule(club.schedule_text, date_from, date_to)
        events: list[Event] = []
        for dt in occurrences:
            events.append(
                Event(
                    id=Event.make_id(club.name, dt),
                    title=club.name,
                    description=f"Recurring run club. {club.schedule_text[:200]}",
                    start_dt=dt,
                    venue_name="",
                    address=club.address if club.address != "Toronto, ON" else "",
                    city="Toronto",
                    url=club.url,
                    source=EventSource.MEETUP,  # closest semantic match
                    price="Free",
                    image_url=club.image_url,
                )
            )
        return events

    # ── LLM fallback ─────────────────────────────────────────────────

    async def _llm_fallback(
        self, page_text: str, date_from: datetime, date_to: datetime
    ) -> list[Event]:
        try:
            from core.llm_extractor import LLMExtractor  # noqa: PLC0415
            extractor = LLMExtractor()
            return await extractor.extract_events(
                raw_text=page_text[:4000],
                source=EventSource.MEETUP,
                source_url=_URL,
                today=date_from,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("runclubs.ca LLM fallback also failed")
            return []


# ── Schedule text parser ──────────────────────────────────────────────────────

def _parse_schedule(
    text: str, date_from: datetime, date_to: datetime
) -> list[datetime]:
    """
    Extract weekday + time from a schedule string and return all matching
    datetimes within [date_from, date_to].

    Handles patterns like:
      "Every Saturday at 9:00 AM"
      "Tuesdays & Thursdays at 6:30 PM"
      "Wed 7pm"
    """
    text_lower = text.lower()

    # Find all mentioned weekdays
    weekdays: list[int] = []
    for name, wday in _DAY_MAP.items():
        if re.search(rf"\b{name}\b", text_lower):
            if wday not in weekdays:
                weekdays.append(wday)

    if not weekdays:
        return []

    # Find time (e.g. "9:00 AM", "6:30pm", "7pm", "18:30")
    hour, minute = 9, 0  # sensible default for a morning run
    time_match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        text_lower,
    )
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = time_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

    # Generate all occurrences in window
    occurrences: list[datetime] = []
    cursor = date_from.replace(hour=hour, minute=minute, second=0, microsecond=0)
    while cursor <= date_to:
        if cursor.weekday() in weekdays and cursor >= date_from:
            occurrences.append(cursor)
        cursor += timedelta(days=1)

    return occurrences
