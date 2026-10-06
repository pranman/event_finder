"""Connector regressions exercise real organizer metadata without network access."""

from datetime import date, time
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx

from ingestion.connectors import common, ical, jsonld

FIXTURES = Path(__file__).parent / "fixtures"


def source(url="https://example.org/events", **config):
    return SimpleNamespace(url=url, config=config, city=SimpleNamespace(timezone="Europe/London"))


def markup(*events):
    return '<script type="application/ld+json">' + json.dumps({"@graph": list(events)}).replace("</", "<\\/") + "</script>"


def calendar(*events):
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Tests//EN\n" + "\n".join(events) + "\nEND:VCALENDAR\n").encode()


def vevent(uid, start, extra="", title="A public event"):
    return f"BEGIN:VEVENT\nUID:{uid}\nDTSTART{start}\nSUMMARY:{title}\n{extra}\nEND:VEVENT"


class ConnectorTests(TestCase):
    def mock_http(self, pages):
        self.requests = []

        def respond(request):
            self.requests.append(str(request.url))
            value = pages[str(request.url)]
            if isinstance(value, httpx.Response):
                return value
            return httpx.Response(200, content=value)

        client = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False)
        self.addCleanup(client.close)
        return patch("ingestion.connectors.common.client", return_value=client)

    def test_real_conway_jsonld_metadata(self):
        url = "https://www.conwayhall.org.uk/whats-on/event/why-pubs-matter/"
        with self.mock_http({url: (FIXTURES / "conway_event.html").read_bytes()}):
            event, = jsonld.fetch(source(url), date(2026, 11, 1), date(2026, 11, 30))
        self.assertEqual(event.title, "Ethical Matters: Why Pubs Matter")
        self.assertEqual((event.start_date, event.start_time), (date(2026, 11, 18), time(18, 30)))
        self.assertEqual(event.end_time, time(20))
        self.assertEqual(event.venue_name, "Conway Hall")
        self.assertEqual(event.price_status, "unknown")
        self.assertEqual(event.external_id, url)
        self.assertIn("WC1R 4RL", event.address)

    def test_jsonld_index_pagination_restricts_host_and_deduplicates_links(self):
        index = '<a href="/event/1">First</a><a href="/event/1">Again</a><a href="https://elsewhere.test/event/2">Foreign</a><a href="javascript:evil()">Unsafe</a><a class="next" href="/page2">Next</a>'
        event = {"@type": "MusicEvent", "name": "Local music", "startDate": "2026-10-10"}
        pages = {"https://example.org/events": index, "https://example.org/event/1": markup(event),
                 "https://example.org/page2": '<a href="/event/2">Second</a>',
                 "https://example.org/event/2": markup(dict(event, name="Second event"))}
        with self.mock_http(pages):
            results = jsonld.fetch(source(link_selector='a[href*="event/"]', next_page_selector="a.next", max_pages=2), date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.requests), 4)
        self.assertIsNone(results[0].start_time)

    def test_jsonld_normalizes_offsets_strips_html_and_rejects_unsafe_links(self):
        event = {"@type": "Event", "name": "<b>Evening</b>", "startDate": "2026-10-06T23:30:00Z",
                 "description": "<p>Hello <strong>world</strong></p><script>alert(1)</script>",
                 "url": "javascript:alert(1)", "image": "data:image/png;base64,abc",
                 "offers": {"price": "12.50", "priceCurrency": "GBP"}}
        with self.mock_http({"https://example.org/events": markup(event)}):
            result, = jsonld.fetch(source(), date(2026, 10, 7), date(2026, 10, 7))
        self.assertEqual((result.start_date, result.start_time), (date(2026, 10, 7), time(0, 30)))
        self.assertEqual(result.description, "Hello world")
        self.assertEqual(result.url, "https://example.org/events")
        self.assertEqual(result.image_url, "")
        self.assertEqual((result.price_status, result.price_amount, result.currency), ("paid", Decimal("12.50"), "GBP"))

    def test_multiple_occurrences_keep_distinct_stable_ids(self):
        events = [{"@type": "Event", "name": "Show", "startDate": day} for day in ("2026-10-10", "2026-10-11")]
        with self.mock_http({"https://example.org/events": markup(*events)}):
            result = jsonld.fetch(source(), date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(set(item.external_id for item in result)), 2)
        self.assertTrue(all(item.external_id.startswith("https://example.org/events#") for item in result))

    def test_missing_metadata_is_not_reported_as_a_successful_empty_import(self):
        with self.mock_http({"https://example.org/events": "<html>Layout has changed</html>"}):
            with self.assertRaises(common.SourceFormatError):
                jsonld.fetch(source(), date(2026, 10, 1), date(2026, 10, 31))
        with self.mock_http({"https://example.org/events": "<html>No upcoming events</html>"}):
            self.assertEqual(jsonld.fetch(source(allow_empty=True), date(2026, 10, 1), date(2026, 10, 31)), [])

    def test_http_failure_and_cross_host_redirect_propagate(self):
        for response, exception in [(httpx.Response(503), httpx.HTTPStatusError),
                                    (httpx.Response(302, headers={"location": "https://other.test/"}), common.SourceFormatError)]:
            with self.subTest(response=response.status_code), self.mock_http({"https://example.org/events": response}):
                with self.assertRaises(exception):
                    jsonld.fetch(source(), date(2026, 10, 1), date(2026, 10, 31))

    def test_real_imperial_calendar_preserves_london_time_and_uid(self):
        url = "https://www.imperial.ac.uk/events/213763/imperial-lates-robots/event.ics"
        with self.mock_http({url: (FIXTURES / "imperial_event.ics").read_bytes()}):
            event, = ical.fetch(source(url), date(2026, 10, 1), date(2026, 11, 1))
        self.assertEqual(event.title, "Imperial Lates: Robots")
        self.assertEqual((event.start_date, event.start_time, event.end_time), (date(2026, 10, 29), time(18), time(21)))
        self.assertEqual(event.external_id, "96ebc8047f064682add4dc3989689578")
        self.assertEqual(event.url, url.removesuffix("event.ics"))
        self.assertEqual(event.price_status, "unknown")

    def test_calendar_index_builds_bounded_same_host_export_urls(self):
        pages = {"https://example.org/events": '<a href="/event/1/">First</a><a href="/event/2/">Second</a>',
                 "https://example.org/event/1/event.ics": calendar(vevent("one", ":20261008T180000Z"))}
        with self.mock_http(pages):
            results = ical.fetch(source(link_selector="a", calendar_suffix="event.ics", max_details=1), date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(results[0].url, "https://example.org/event/1/")

    def test_all_day_exclusive_end_and_missing_end_are_preserved(self):
        data = calendar(vevent("multi", ";VALUE=DATE:20261006", "DTEND;VALUE=DATE:20261009"),
                        vevent("single", ";VALUE=DATE:20261008"))
        events = ical.parse_calendar(data, "https://example.org/events", source(), date(2026, 10, 8), date(2026, 10, 8))
        by_id = {event.external_id: event for event in events}
        self.assertEqual(by_id["multi"].end_date, date(2026, 10, 8))
        self.assertIsNone(by_id["multi"].start_time)
        self.assertIsNone(by_id["single"].end_date)
        self.assertIsNone(by_id["single"].start_time)

    def test_recurrence_exclusions_and_dst_keep_local_times(self):
        data = calendar(vevent("weekly", ";TZID=Europe/London:20261011T183000",
                              "RRULE:FREQ=WEEKLY;COUNT=4\nEXDATE;TZID=Europe/London:20261018T183000\nDTEND;TZID=Europe/London:20261011T193000"))
        results = ical.parse_calendar(data, "https://example.org/events", source(), date(2026, 10, 1), date(2026, 11, 1))
        self.assertEqual([event.start_date for event in results], [date(2026, 10, 11), date(2026, 10, 25), date(2026, 11, 1)])
        self.assertTrue(all(event.start_time == time(18, 30) for event in results))
        self.assertEqual(len({event.external_id for event in results}), 3)

    def test_moved_recurrence_keeps_original_occurrence_identity(self):
        data = calendar(vevent("weekly", ";TZID=Europe/London:20261018T183000",
                              "RRULE:FREQ=WEEKLY;COUNT=2\nDTEND;TZID=Europe/London:20261018T193000"),
                        vevent("weekly", ";TZID=Europe/London:20261026T200000",
                              "RECURRENCE-ID;TZID=Europe/London:20261025T183000\nDTEND;TZID=Europe/London:20261026T210000", "Moved"))
        results = ical.parse_calendar(data, "https://example.org/events", source(), date(2026, 10, 1), date(2026, 11, 1))
        moved = next(event for event in results if event.title == "Moved")
        self.assertEqual(moved.external_id, "weekly#2026-10-25T18:30:00+00:00")
        self.assertEqual((moved.start_date, moved.start_time), (date(2026, 10, 26), time(20)))

    def test_cancelled_calendar_event_remains_visible_as_cancelled(self):
        data = calendar(vevent("cancelled", ":20261008T180000Z", "STATUS:CANCELLED"))
        event, = ical.parse_calendar(data, "https://example.org/events", source(), date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(event.status, "cancelled")

    def test_infinite_recurrence_is_bounded(self):
        data = calendar(vevent("frequent", ":20261008T180000Z", "RRULE:FREQ=SECONDLY"))
        with self.assertRaisesRegex(common.SourceFormatError, "event limit"):
            ical.parse_calendar(data, "https://example.org/events", source(max_events=5), date(2026, 10, 8), date(2026, 10, 8))

    def test_valid_empty_calendar_is_successful_but_html_is_not(self):
        self.assertEqual(ical.parse_calendar(calendar(), "https://example.org/events", source(), date(2026, 10, 1), date(2026, 10, 31)), [])
        with self.assertRaises(common.SourceFormatError):
            ical.parse_calendar(b"<html>Service unavailable</html>", "https://example.org/events", source(), date(2026, 10, 1), date(2026, 10, 31))
