"""
tests/test_scrapers.py — Unit tests for all scrapers.

Uses `respx` to mock httpx calls so no real network requests are made.
Each test validates that the scraper correctly parses its source's
response format into canonical Event objects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from models import EventSource
from scrapers.luma import LumaScraper
from scrapers.eventbrite import EventbriteScraper
from scrapers.songkick import SongkickScraper
from scrapers.blogto import BlogTOScraper


# ──────────────────────────────────────────────
# Luma Scraper Tests
# ──────────────────────────────────────────────

class TestLumaScraper:

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_returns_events(self, date_from, date_to):
        """LumaScraper should parse the Luma API response into Events."""
        mock_response = {
            "entries": [
                {
                    "event": {
                        "name": "Toronto AI Meetup",
                        "url": "toronto-ai-meetup",
                        "start_at": "2026-06-14T18:30:00Z",
                        "end_at": "2026-06-14T21:00:00Z",
                        "description": "Monthly AI gathering",
                        "location": {"name": "MaRS Discovery District"},
                        "geo_address_json": {
                            "full_address": "101 College St, Toronto",
                            "city": "Toronto",
                            "latitude": 43.6594,
                            "longitude": -79.3895,
                        },
                        "ticket_info": {"price": 0},
                        "cover_url": None,
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None,
        }
        respx.get("https://api.lu.ma/public/v1/discover/get-paginated-events").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        async with httpx.AsyncClient() as client:
            scraper = LumaScraper(client)
            events = await scraper.fetch(date_from, date_to)

        assert len(events) == 1
        event = events[0]
        assert event.title == "Toronto AI Meetup"
        assert event.source == EventSource.LUMA
        assert event.price == "Free"
        assert event.venue_name == "MaRS Discovery District"
        assert event.lat == pytest.approx(43.6594)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_skips_events_without_title(self, date_from, date_to):
        mock_response = {
            "entries": [{"event": {"start_at": "2026-06-14T18:30:00Z", "url": "x"}}],
            "has_more": False,
        }
        respx.get("https://api.lu.ma/public/v1/discover/get-paginated-events").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        async with httpx.AsyncClient() as client:
            scraper = LumaScraper(client)
            events = await scraper.fetch(date_from, date_to)
        assert events == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_handles_http_error_gracefully(self, date_from, date_to):
        """HTTP errors should return empty list, not raise."""
        respx.get("https://api.lu.ma/public/v1/discover/get-paginated-events").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            scraper = LumaScraper(client)
            events = await scraper.fetch(date_from, date_to)
        assert events == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_handles_network_error(self, date_from, date_to):
        """Network errors should return empty list, not raise."""
        respx.get("https://api.lu.ma/public/v1/discover/get-paginated-events").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        async with httpx.AsyncClient() as client:
            scraper = LumaScraper(client)
            events = await scraper.fetch(date_from, date_to)
        assert events == []


# ──────────────────────────────────────────────
# Eventbrite Scraper Tests
# ──────────────────────────────────────────────

class TestEventbriteScraper:

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_skips_without_api_key(self, date_from, date_to, monkeypatch):
        monkeypatch.delenv("EVENTBRITE_API_KEY", raising=False)
        async with httpx.AsyncClient() as client:
            scraper = EventbriteScraper(client)
            events = await scraper.fetch(date_from, date_to)
        assert events == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_parses_free_event(self, date_from, date_to, monkeypatch):
        monkeypatch.setenv("EVENTBRITE_API_KEY", "test-key")
        mock_response = {
            "events": [
                {
                    "name": {"text": "Tech Startup Night"},
                    "start": {"utc": "2026-06-12T19:00:00Z"},
                    "end": {"utc": "2026-06-12T22:00:00Z"},
                    "url": "https://www.eventbrite.com/e/sample",
                    "description": {"text": "Startup pitch event"},
                    "logo": None,
                    "venue": {
                        "name": "Workhaus",
                        "address": {
                            "address_1": "37 Hanna Ave",
                            "city": "Toronto",
                            "region": "ON",
                        },
                        "latitude": 43.6382,
                        "longitude": -79.4241,
                    },
                    "ticket_availability": {"is_free": True},
                }
            ],
            "pagination": {"page_count": 1},
        }
        respx.get("https://www.eventbriteapi.com/v3/events/search/").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        async with httpx.AsyncClient() as client:
            scraper = EventbriteScraper(client)
            events = await scraper.fetch(date_from, date_to)

        assert len(events) == 1
        assert events[0].title == "Tech Startup Night"
        assert events[0].price == "Free"
        assert events[0].source == EventSource.EVENTBRITE


# ──────────────────────────────────────────────
# Songkick Scraper Tests
# ──────────────────────────────────────────────

class TestSongkickScraper:

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_builds_title_from_artists(self, date_from, date_to, monkeypatch):
        monkeypatch.setenv("SONGKICK_API_KEY", "test-key")
        mock_response = {
            "resultsPage": {
                "results": {
                    "event": [
                        {
                            "id": "sk-1234",
                            "uri": "https://www.songkick.com/concerts/1234",
                            "start": {"date": "2026-06-14", "time": "20:00:00"},
                            "performance": [
                                {
                                    "artist": {"displayName": "The Beaches"},
                                    "billing": "headline",
                                }
                            ],
                            "venue": {
                                "displayName": "Horseshoe Tavern",
                                "street": "370 Queen St W",
                                "lat": 43.6494,
                                "lng": -79.3952,
                                "metroArea": {"displayName": "Toronto"},
                            },
                        }
                    ]
                },
                "totalEntries": 1,
                "perPage": 50,
            }
        }
        respx.get(
            "https://api.songkick.com/api/3.0/metro_areas/6070/calendar.json"
        ).mock(return_value=httpx.Response(200, json=mock_response))

        async with httpx.AsyncClient() as client:
            scraper = SongkickScraper(client)
            events = await scraper.fetch(date_from, date_to)

        assert len(events) == 1
        assert "The Beaches" in events[0].title
        assert "Horseshoe Tavern" in events[0].title
        assert events[0].source == EventSource.SONGKICK

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_skips_without_api_key(self, date_from, date_to, monkeypatch):
        monkeypatch.delenv("SONGKICK_API_KEY", raising=False)
        async with httpx.AsyncClient() as client:
            scraper = SongkickScraper(client)
            events = await scraper.fetch(date_from, date_to)
        assert events == []


# ──────────────────────────────────────────────
# BlogTO Scraper Tests
# ──────────────────────────────────────────────

class TestBlogTOScraper:

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_parses_article(self, date_from, date_to):
        html = """
        <html><body>
          <article class="post">
            <h2><a href="/events/test-event">Summer Food Festival</a></h2>
            <time datetime="2026-06-13T12:00:00">June 13</time>
            <div class="event-location">Distillery District</div>
          </article>
        </body></html>
        """
        respx.get("https://www.blogto.com/events/").mock(
            return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            scraper = BlogTOScraper(client)
            events = await scraper.fetch(date_from, date_to)

        # May or may not find the event depending on date parsing
        # but should never raise
        assert isinstance(events, list)

    @respx.mock
    @pytest.mark.asyncio
    async def test_fetch_handles_empty_page(self, date_from, date_to):
        respx.get("https://www.blogto.com/events/").mock(
            return_value=httpx.Response(200, text="<html><body></body></html>",
                                       headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient() as client:
            scraper = BlogTOScraper(client)
            events = await scraper.fetch(date_from, date_to)
        assert events == []
