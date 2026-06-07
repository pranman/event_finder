"""
scrapers/blogto.py — BlogTO events page scraper.

BlogTO (blogto.com/events) curates the best upcoming Toronto events.
This scraper uses BeautifulSoup to extract structured event data from
their public events listing. No API key required.

The LLM extractor (core/llm_extractor.py) is used as a fallback to parse
events whose HTML structure doesn't cleanly match our selectors.
"""

from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any

from bs4 import BeautifulSoup, Tag

from models import Event, EventSource
from scrapers.base import BaseScraper

_BLOGTO_EVENTS_URL = "https://www.blogto.com/events/"


class BlogTOScraper(BaseScraper):
    """Scrapes curated Toronto events from BlogTO."""

    SOURCE_NAME = "blogto"

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        resp = await self._client.get(
            _BLOGTO_EVENTS_URL,
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
        events: list[Event] = []

        # BlogTO wraps each event in an <article> tag with class "post"
        for article in soup.select("article.post, li.event-item, div.event-card"):
            parsed = self._parse_article(article, date_from, date_to)
            if parsed:
                events.append(parsed)

        self._log.info("BlogTO: fetched %d events", len(events))
        return events

    # ── private ───────────────────────────────────────────────────────

    def _parse_article(
        self, tag: Tag, date_from: datetime, date_to: datetime
    ) -> Event | None:
        try:
            title_tag = tag.select_one("h2 a, h3 a, .post-title a, .entry-title a")
            if not title_tag:
                return None
            title = title_tag.get_text(strip=True)
            url = title_tag.get("href", "")
            if not url.startswith("http"):
                url = f"https://www.blogto.com{url}"

            # Date — BlogTO embeds structured data or <time> tags
            time_tag = tag.select_one("time[datetime], .event-date, .post-date")
            date_str = (
                (time_tag.get("datetime") or time_tag.get_text(strip=True))
                if time_tag
                else None
            )
            start_dt = self._parse_date(date_str, date_from)
            if not start_dt:
                return None
            if not (date_from <= start_dt <= date_to):
                return None

            venue_tag = tag.select_one(".venue, .location, .event-location")
            venue = venue_tag.get_text(strip=True) if venue_tag else ""

            img_tag = tag.select_one("img[src]")
            image_url = img_tag.get("src") if img_tag else None

            return Event(
                id=Event.make_id(title, start_dt),
                title=title,
                description="",
                start_dt=start_dt,
                venue_name=venue,
                address=venue,
                city="Toronto",
                url=url,
                source=EventSource.BLOGTO,
                price="Unknown",
                image_url=str(image_url) if image_url else None,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to parse BlogTO article")
            return None

    @staticmethod
    def _parse_date(date_str: str | None, fallback: datetime) -> datetime | None:
        """Attempt to parse a date string into a datetime."""
        if not date_str:
            return None
        # Handle ISO format from <time datetime="...">
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Handle "June 14" or "Jun 14, 2024" style strings
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d", "%b %d"):
            try:
                parsed = datetime.strptime(date_str.strip(), fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=fallback.year)
                return parsed
            except ValueError:
                continue
        return None
