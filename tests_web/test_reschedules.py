"""Existing provider identities must survive moves outside discovery dates."""

from dataclasses import replace
from datetime import date, time
import json
from unittest.mock import patch

from django.test import TestCase
import httpx

from events.models import City, Event, Source, SourceListing
from ingestion.service import import_source
from ingestion.types import EventPayload


def markup(*events):
    return '<script type="application/ld+json">' + json.dumps(list(events)) + "</script>"


def calendar(*events):
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Tests//EN\n" + "\n".join(events) + "\nEND:VCALENDAR\n").encode()


def vevent(uid, start, extra="", title="A public event"):
    summary = f"SUMMARY:{title}\n" if title is not None else ""
    dtstart = f"DTSTART{start}\n" if start is not None else ""
    return f"BEGIN:VEVENT\nUID:{uid}\n{dtstart}{summary}{extra}\nEND:VEVENT"


class RescheduleTests(TestCase):
    def setUp(self):
        self.city = City.objects.create(name="City", slug="city", country_code="GB", timezone="Europe/London", currency="GBP")
        self.source = Source.objects.create(name="Provider", slug="provider", city=self.city, connector="jsonld", url="https://example.org/events")
        self.date_from, self.date_to = date(2026, 10, 1), date(2026, 10, 31)

    def import_body(self, body, connector):
        self.source.connector = connector
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
        with patch("ingestion.connectors.common.client", return_value=client):
            result = import_source(self.source, self.date_from, self.date_to)
        self.assertEqual(result.status, "success", result.error)
        return result

    def json_event(self, identifier, start, **extra):
        return {"@type": "Event", "identifier": identifier, "name": identifier,
                "startDate": start, **extra}

    def test_service_reconciles_known_ids_but_rejects_unknown_outside_window(self):
        record = EventPayload("known", "Talk", "https://example.org/talk", date(2026, 10, 10), time(18))
        import_source(self.source, self.date_from, self.date_to, connector=lambda *_: [record])
        event_id = Event.objects.get().pk
        moved = replace(record, start_date=date(2027, 1, 5))
        unknown = replace(moved, external_id="unknown", title="Undiscovered")
        result = import_source(self.source, self.date_from, self.date_to, connector=lambda *_: [moved, unknown])
        self.assertEqual((result.status, result.updated_count, result.created_count), ("success", 1, 0))
        self.assertEqual(Event.objects.get().pk, event_id)
        self.assertEqual(Event.objects.get().start_date, moved.start_date)
        self.assertEqual(SourceListing.objects.get().external_id, "known")

    def test_known_identity_context_is_scoped_to_source_and_stored_window(self):
        current = EventPayload("current", "Current", "https://example.org/current", date(2026, 10, 10))
        past = replace(current, external_id="past", start_date=date(2026, 9, 10))
        import_source(self.source, date(2026, 9, 1), self.date_to, connector=lambda *_: [current, past])
        other = Source.objects.create(name="Other", slug="other", city=self.city, connector="jsonld", url="https://other.example/events")
        import_source(other, self.date_from, self.date_to, connector=lambda *_: [replace(current, external_id="other")])
        seen = []

        def fetch(source, *_):
            seen.append(source.known_external_ids)
            return []

        import_source(self.source, self.date_from, self.date_to, connector=fetch)
        self.assertEqual(seen, [frozenset({"current"})])
        self.assertFalse(Event.objects.exclude(status="scheduled").exists())

    def test_jsonld_move_later_updates_existing_event_without_expanding_discovery(self):
        self.import_body(markup(self.json_event("known", "2026-10-10")), "jsonld")
        identity = Event.objects.get().pk
        result = self.import_body(markup(self.json_event("known", "2026-11-20"), self.json_event("unknown", "2026-11-21")), "jsonld")
        self.assertEqual((result.received_count, result.updated_count), (1, 1))
        self.assertEqual(Event.objects.get().pk, identity)
        self.assertEqual(Event.objects.get().start_date, date(2026, 11, 20))
        self.assertEqual(SourceListing.objects.count(), 1)

    def test_jsonld_move_earlier_with_cancellation_updates_existing_event(self):
        self.import_body(markup(self.json_event("known", "2026-10-10")), "jsonld")
        self.import_body(markup(self.json_event("known", "2026-09-20", eventStatus="https://schema.org/EventCancelled")), "jsonld")
        event = Event.objects.get()
        self.assertEqual((event.start_date, event.status), (date(2026, 9, 20), "cancelled"))

    def test_ical_single_events_moved_before_or_after_window_keep_uid(self):
        for new_date in ("20260920", "20261120"):
            with self.subTest(new_date=new_date):
                Event.objects.all().delete()
                self.import_body(calendar(vevent("known", ":20261010T180000Z")), "ical")
                identity = Event.objects.get().pk
                result = self.import_body(calendar(vevent("known", f":{new_date}T180000Z"),
                                                   vevent("unknown", ":20261121T180000Z")), "ical")
                self.assertEqual((result.updated_count, result.created_count), (1, 0))
                self.assertEqual(Event.objects.get().pk, identity)
                self.assertEqual(Event.objects.get().start_date.strftime("%Y%m%d"), new_date)
                self.assertEqual(SourceListing.objects.get().external_id, "known")

    def test_ical_moved_recurrence_inherits_duration_and_preserves_original_slot(self):
        self.date_from, self.date_to = date(2026, 10, 24), date(2026, 10, 31)
        master = vevent("weekly", ";TZID=Europe/London:20261011T183000",
                        "RRULE:FREQ=WEEKLY;COUNT=3\nDTEND;TZID=Europe/London:20261011T193000")
        self.import_body(calendar(master), "ical")
        listing = SourceListing.objects.get()
        self.assertEqual(listing.external_id, "weekly#2026-10-25T18:30:00+00:00")
        moved = vevent("weekly", ";TZID=Europe/London:20261103T200000",
                       "RECURRENCE-ID;TZID=Europe/London:20261025T183000", title=None)
        result = self.import_body(calendar(master, moved, vevent("unknown", ":20261108T180000Z")), "ical")
        self.assertEqual((result.received_count, result.updated_count), (1, 1))
        listing.refresh_from_db()
        self.assertEqual(listing.event.start_date, date(2026, 11, 3))
        self.assertEqual((listing.event.start_time, listing.event.end_time), (time(20), time(21)))
        self.assertEqual(listing.event.title, "A public event")
        self.assertEqual(SourceListing.objects.count(), 1)

    def test_ical_cancelled_moved_recurrence_updates_known_session(self):
        self.date_from, self.date_to = date(2026, 10, 24), date(2026, 10, 31)
        master = vevent("weekly", ";TZID=Europe/London:20261011T183000",
                        "RRULE:FREQ=WEEKLY;COUNT=3\nDTEND;TZID=Europe/London:20261011T193000")
        self.import_body(calendar(master), "ical")
        cancelled = vevent("weekly", ";TZID=Europe/London:20260920T200000",
                           "RECURRENCE-ID;TZID=Europe/London:20261025T183000\nSTATUS:CANCELLED", title=None)
        result = self.import_body(calendar(master, cancelled), "ical")
        self.assertEqual((result.received_count, result.updated_count), (1, 1))
        event = Event.objects.get()
        self.assertEqual((event.start_date, event.status), (date(2026, 9, 20), "cancelled"))

    def test_ical_cancelled_override_can_refer_only_to_original_slot(self):
        self.date_from, self.date_to = date(2026, 10, 24), date(2026, 10, 31)
        master = vevent("weekly", ";TZID=Europe/London:20261011T183000",
                        "RRULE:FREQ=WEEKLY;COUNT=3\nDTEND;TZID=Europe/London:20261011T193000")
        self.import_body(calendar(master), "ical")
        cancelled = vevent("weekly", None, "RECURRENCE-ID;TZID=Europe/London:20261025T183000\nSTATUS:CANCELLED", title=None)
        self.import_body(calendar(master, cancelled), "ical")
        self.assertEqual(Event.objects.get().status, "cancelled")
        self.assertEqual(Event.objects.get().start_date, date(2026, 10, 25))
