"""
scrapers/reddit.py — Reddit r/toronto events thread parser.

Reads the weekly "What's happening in Toronto this weekend?" megathread
and any other recent event posts from r/toronto. Uses the Reddit PRAW
library for authenticated API access (free tier).

Requires: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT in .env

The raw text of each post is sent to the LLM extractor to pull out
structured event data (date, venue, etc.) from unstructured Reddit prose.
"""

from __future__ import annotations

import os
import asyncio
from datetime import datetime
from typing import Any

from models import Event, EventSource
from scrapers.base import BaseScraper

# Search terms to find event-related posts in r/toronto
_EVENT_SEARCH_TERMS = [
    "what's happening",
    "weekend events",
    "things to do",
    "event",
    "this weekend",
]


class RedditScraper(BaseScraper):
    """
    Fetches event information from r/toronto Reddit posts.

    Note: PRAW is synchronous, so we run it in an executor to avoid
    blocking the async event loop.
    """

    SOURCE_NAME = "reddit"

    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
        client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
        user_agent = os.getenv(
            "REDDIT_USER_AGENT", "events_finder/1.0 by your-username"
        ).strip()

        if not client_id or not client_secret:
            self._log.warning("Reddit credentials not set — skipping Reddit")
            return []

        # Run PRAW (sync) in a thread pool so we don't block asyncio
        loop = asyncio.get_event_loop()
        raw_posts = await loop.run_in_executor(
            None,
            self._fetch_posts_sync,
            client_id,
            client_secret,
            user_agent,
            date_from,
            date_to,
        )
        self._log.info("Reddit: found %d candidate posts", len(raw_posts))
        return raw_posts

    def _fetch_posts_sync(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        date_from: datetime,
        date_to: datetime,
    ) -> list[Event]:
        """Synchronous PRAW fetching — runs in executor."""
        try:
            import praw  # lazy import so missing dep doesn't crash at startup
        except ImportError:
            self._log.error("praw not installed — run: pip install praw")
            return []

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

        subreddit = reddit.subreddit("toronto")
        events: list[Event] = []

        # 1) Search for event megathreads and posts
        for term in _EVENT_SEARCH_TERMS:
            try:
                for submission in subreddit.search(term, limit=5, time_filter="week"):
                    # Store raw text for LLM extraction later
                    body = submission.selftext or submission.title
                    post_url = f"https://www.reddit.com{submission.permalink}"

                    # Create a placeholder event — the LLM extractor will
                    # enrich these with proper dates/venues later.
                    event = Event(
                        id=Event.make_id(submission.title, date_from),
                        title=submission.title[:120],
                        description=body[:2000],
                        start_dt=date_from,  # placeholder
                        url=post_url,
                        source=EventSource.REDDIT,
                        raw_text=f"{submission.title}\n\n{body}",
                    )
                    events.append(event)
            except Exception:  # noqa: BLE001
                self._log.debug("Error searching Reddit for: %s", term)
                continue

        # Deduplicate by URL
        seen: set[str] = set()
        unique: list[Event] = []
        for e in events:
            if e.url not in seen:
                seen.add(e.url)
                unique.append(e)

        return unique
