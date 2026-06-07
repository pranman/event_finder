"""
scrapers/instagram.py — Instagram account watcher.

Fetches recent posts from the accounts listed in config['instagram_accounts'],
extracts event details from captions, and produces canonical Event objects.

Requires:  instaloader>=4.10  (pip install instaloader)
Optional:  INSTAGRAM_SESSION_FILE env var → path to a saved instaloader session
           for more reliable access.  Save one with:
               instaloader --login <your_ig_username>
           which writes ~/.config/instaloader/session-<username>.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from models import Event, EventSource
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

try:
    import instaloader  # type: ignore[import-untyped]
    _HAS_INSTALOADER = True
except ImportError:
    _HAS_INSTALOADER = False

_DAY_MAP: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_EVENT_SIGNALS = [
    "join us", "come join", "run with us", "meet us", "meet at",
    "this monday", "this tuesday", "this wednesday", "this thursday",
    "this friday", "this saturday", "this sunday",
    "next monday", "next tuesday", "next wednesday", "next thursday",
    "every monday", "every tuesday", "every wednesday",
    "register", "sign up", "rsvp", "link in bio",
    "free", "event", "workshop", "yoga", "run", "walk", "class",
    "session", "paddle", "kayak", "fitness", "workout",
]


class InstagramScraper(BaseScraper):
    """
    Watches Instagram accounts for upcoming event posts.

    Accounts are read from config['instagram_accounts'].
    Session file path from env INSTAGRAM_SESSION_FILE.
    """

    SOURCE_NAME = "instagram"

    def __init__(self, client: httpx.AsyncClient, config: dict[str, Any]) -> None:
        super().__init__(client)
        self._accounts: list[dict] = config.get("instagram_accounts", [])
        self._session_file = os.getenv("INSTAGRAM_SESSION_FILE", "")
        self._max_posts = 20   # per account — polite ceiling

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        if not _HAS_INSTALOADER:
            self._log.warning(
                "instaloader not installed — run: pip install instaloader"
            )
            return []

        if not self._accounts:
            return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._fetch_sync, date_from, date_to
        )

    def _fetch_sync(self, date_from: datetime, date_to: datetime) -> list[Event]:
        loader = self._make_loader()
        events: list[Event] = []

        for account in self._accounts:
            handle = account.get("handle", "").lstrip("@")
            if not handle:
                continue
            try:
                batch = self._scrape_account(loader, handle, account, date_from, date_to)
                self._log.info("@%s → %d events", handle, len(batch))
                events.extend(batch)
            except Exception:  # noqa: BLE001
                self._log.exception("Failed scraping @%s — skipping", handle)

        return events

    def _make_loader(self) -> Any:
        loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True,
        )
        session_path = self._session_file
        if session_path and os.path.exists(session_path):
            try:
                # Session filename convention: session-<username>
                username = os.path.basename(session_path).replace("session-", "").split(".")[0]
                loader.load_session_from_file(username, session_path)
                self._log.info("Loaded Instagram session from %s", session_path)
            except Exception:  # noqa: BLE001
                self._log.warning("Could not load session file — continuing unauthenticated")
        return loader

    def _scrape_account(
        self,
        loader: Any,
        handle: str,
        account: dict,
        date_from: datetime,
        date_to: datetime,
    ) -> list[Event]:
        profile = instaloader.Profile.from_username(loader.context, handle)
        profile_url = f"https://www.instagram.com/{handle}/"
        bio_url = profile.external_url or profile_url

        events: list[Event] = []
        # Look back 30 days — posts about next week's event were posted last week
        post_cutoff = (date_from - timedelta(days=30)).replace(tzinfo=None)

        for post in profile.get_posts():
            if post.date_utc < post_cutoff:
                break   # posts are newest-first; stop early
            if len(events) >= self._max_posts:
                break

            caption = (post.caption or "").strip()
            if not caption:
                continue

            post_url = f"https://www.instagram.com/p/{post.shortcode}/"
            extracted = self._events_from_caption(
                caption=caption,
                post_url=post_url,
                bio_url=bio_url,
                account=account,
                post_date=post.date_utc.replace(tzinfo=timezone.utc),
                date_from=date_from,
                date_to=date_to,
            )
            events.extend(extracted)

        return events

    def _events_from_caption(
        self,
        caption: str,
        post_url: str,
        bio_url: str,
        account: dict,
        post_date: datetime,
        date_from: datetime,
        date_to: datetime,
    ) -> list[Event]:
        caption_lower = caption.lower()

        # Skip posts that don't look event-related
        if not any(sig in caption_lower for sig in _EVENT_SIGNALS):
            return []

        event_dt = self._parse_event_date(caption_lower, post_date, date_from, date_to)
        if event_dt is None:
            return []

        price = "Free" if re.search(r"\bfree\b", caption_lower) else "Unknown"
        label = account.get("label", f"@{account.get('handle', 'unknown')}")
        title = self._extract_title(caption, label)

        # Use bio URL if it looks like a registration/event link rather than generic IG profile
        url = bio_url if bio_url != f"https://www.instagram.com/{account.get('handle', '')}/" else post_url

        return [
            Event(
                id=Event.make_id(title, event_dt),
                title=title,
                description=caption[:500],
                start_dt=event_dt,
                venue_name="",
                address="Toronto, ON",
                city="Toronto",
                url=url,
                source=EventSource.INSTAGRAM,
                price=price,
                raw_text=caption,
            )
        ]

    def _parse_event_date(
        self,
        caption_lower: str,
        post_date: datetime,
        date_from: datetime,
        date_to: datetime,
    ) -> datetime | None:
        """Return the nearest upcoming weekday match within [date_from, date_to]."""
        # Try "this/next/every <weekday>"
        for day_name, weekday in _DAY_MAP.items():
            if re.search(rf"\b(this|next|every)?\s*{day_name}\b", caption_lower):
                candidate = self._next_weekday(weekday, date_from)
                if date_from <= candidate <= date_to:
                    return candidate

        # "tomorrow" / "tonight" relative to post date
        if re.search(r"\b(tomorrow|tonight|this week)\b", caption_lower):
            candidate = (post_date + timedelta(days=1)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            if date_from <= candidate <= date_to:
                return candidate

        return None

    @staticmethod
    def _next_weekday(weekday: int, from_dt: datetime) -> datetime:
        """Return the next occurrence of weekday (0=Mon) on or after from_dt."""
        dt = from_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        days_ahead = (weekday - dt.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7   # "every Monday" means next occurrence, not today
        return dt + timedelta(days=days_ahead)

    @staticmethod
    def _extract_title(caption: str, fallback: str) -> str:
        first_line = caption.split("\n")[0].strip()
        cleaned = re.sub(r"[#@]\S+", "", first_line).strip()
        cleaned = re.sub(r"[\U00010000-\U0010ffff]", "", cleaned).strip()   # strip emoji
        return cleaned[:80] if len(cleaned) > 5 else f"{fallback} Event"
