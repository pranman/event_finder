from dataclasses import replace
from datetime import date, time, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from events.models import Category, City, Event, ImportRun, Source, SourceListing
from ingestion.service import import_source
from ingestion.types import EventPayload


class ImportTests(TestCase):
    def setUp(self):
        self.city = City.objects.create(name="Test city", slug="test-city", country_code="GB", timezone="Europe/London", currency="GBP")
        self.source = Source.objects.create(name="Provider", slug="provider", city=self.city, connector="jsonld", url="https://example.org/events", config={"categories": ["talks"]})
        Category.objects.create(name="Talks", slug="talks")
        self.payload = EventPayload("provider-1", "A talk", "https://example.org/event/1", date(2026, 10, 25), time(18), venue_name="Main hall")

    def run_import(self, *payloads, source=None):
        return import_source(source or self.source, date(2026, 10, 1), date(2026, 12, 31), connector=lambda *_: list(payloads))

    def test_repeat_import_is_idempotent_and_records_provenance(self):
        first = self.run_import(self.payload)
        second = self.run_import(self.payload)
        self.assertEqual((first.status, first.created_count), ("success", 1))
        self.assertEqual((second.created_count, second.updated_count), (0, 0))
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(SourceListing.objects.count(), 1)
        self.assertEqual(Event.objects.get().categories.get().slug, "talks")
        self.assertEqual(ImportRun.objects.count(), 2)

    def test_provider_identity_survives_title_and_date_change(self):
        self.run_import(self.payload)
        event = Event.objects.get()
        event.is_published = False
        event.save()
        result = self.run_import(replace(self.payload, title="Renamed", start_date=date(2026, 11, 1)))
        event.refresh_from_db()
        self.assertEqual(result.updated_count, 1)
        self.assertEqual(event.title, "Renamed")
        self.assertFalse(event.is_published)
        self.assertEqual(Event.objects.count(), 1)

    def test_failed_fetch_preserves_existing_records_and_freshness(self):
        self.run_import(self.payload)
        self.source.refresh_from_db()
        previous = self.source.last_success_at
        def fail(*_):
            raise ValueError("Source format changed")
        run = import_source(self.source, date(2026, 10, 1), date(2026, 12, 31), connector=fail)
        self.source.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertEqual(self.source.last_success_at, previous)
        self.assertEqual(Event.objects.count(), 1)
        self.assertIn("Source format changed", run.error)

    def test_empty_success_does_not_cancel_events(self):
        self.run_import(self.payload)
        run = self.run_import()
        self.assertEqual((run.status, run.received_count), ("success", 0))
        self.assertEqual(Event.objects.get().status, "scheduled")

    def test_invalid_batch_rolls_back_all_writes(self):
        bad = replace(self.payload, external_id="bad", title="Bad", end_time=time(17))
        run = self.run_import(self.payload, bad)
        self.assertEqual(run.status, "failed")
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(run.created_count, 0)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_success_at)

    def test_cross_source_matching_requires_city_venue_and_session(self):
        self.run_import(self.payload)
        second = Source.objects.create(name="Other", slug="other", city=self.city, connector="jsonld", url="https://other.example/events")
        self.run_import(replace(self.payload, external_id="other-id"), source=second)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(SourceListing.objects.count(), 2)
        self.run_import(replace(self.payload, external_id="late-session", start_time=time(20)), source=second)
        self.assertEqual(Event.objects.count(), 2)
        other_city = City.objects.create(name="Elsewhere", slug="elsewhere", country_code="FR", timezone="Europe/Paris")
        third = Source.objects.create(name="Third", slug="third", city=other_city, connector="jsonld", url="https://third.example/events")
        self.run_import(self.payload, source=third)
        self.assertEqual(Event.objects.count(), 3)

    def test_divergent_source_reschedule_does_not_move_other_listing(self):
        self.run_import(self.payload)
        other = Source.objects.create(name="Other", slug="other", city=self.city, connector="jsonld", url="https://other.example/events")
        self.run_import(self.payload, source=other)
        self.run_import(replace(self.payload, start_time=time(20)), source=other)
        self.assertEqual(self.source.listings.get().event.start_time, time(18))
        self.assertEqual(other.listings.get().event.start_time, time(20))

    def test_unknown_time_events_are_not_cross_source_merged(self):
        self.run_import(replace(self.payload, start_time=None))
        other = Source.objects.create(name="Other", slug="other", city=self.city, connector="jsonld", url="https://other.example/events")
        self.run_import(replace(self.payload, start_time=None), source=other)
        self.assertEqual(Event.objects.count(), 2)

    def test_cancelled_payload_updates_known_event(self):
        self.run_import(self.payload)
        self.run_import(replace(self.payload, status="cancelled"))
        self.assertEqual(Event.objects.get().status, "cancelled")

    def test_conflicting_ids_fail_instead_of_silently_discarding(self):
        result = self.run_import(self.payload, replace(self.payload, title="Other title"))
        self.assertEqual(result.status, "failed")
        self.assertFalse(Event.objects.exists())

    def test_unsafe_links_and_nonfinite_prices_are_rejected(self):
        for payload in (replace(self.payload, url="javascript:alert(1)"), replace(self.payload, price_amount=Decimal("NaN"), currency="GBP")):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_import(payload).status, "failed")
        self.assertFalse(Event.objects.exists())

    def test_multi_day_events_overlap_requested_window(self):
        record = replace(self.payload, start_date=date(2026, 9, 30), end_date=date(2026, 10, 2))
        self.assertEqual(self.run_import(record).received_count, 1)

    def test_due_command_skips_fresh_sources(self):
        self.source.last_success_at = timezone.now()
        self.source.save()
        with patch("events.management.commands.sync_events.import_source") as importer:
            call_command("sync_events", due=True, stdout=StringIO())
        importer.assert_not_called()

    def test_command_failure_returns_nonzero_after_attempting_other_sources(self):
        Source.objects.create(name="Other", slug="other", city=self.city, connector="jsonld", url="https://other.example/events")
        with patch("ingestion.connectors.CONNECTORS", {"jsonld": lambda *_: (_ for _ in ()).throw(ValueError("broken"))}):
            with self.assertRaises(CommandError):
                call_command("sync_events", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(ImportRun.objects.filter(status="failed").count(), 2)

    def test_invalid_command_options_are_rejected(self):
        for kwargs in ({"city": "missing"}, {"days": 0}, {"source": "missing"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CommandError):
                call_command("sync_events", stdout=StringIO(), **kwargs)
