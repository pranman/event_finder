"""Imported provider identities must remain attached to their original city."""

from dataclasses import replace
from datetime import date, time

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from events.models import City, Event, ImportRun, Source, SourceListing
from ingestion.service import import_source
from ingestion.types import EventPayload


class SourceCityIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.london = City.objects.create(
            name="London", slug="london", country_code="GB", timezone="Europe/London",
        )
        cls.paris = City.objects.create(
            name="Paris", slug="paris", country_code="FR", timezone="Europe/Paris",
        )
        cls.user = get_user_model().objects.create_superuser(
            username="source-admin", email="admin@example.com", password="test-password",
        )

    def setUp(self):
        self.source = Source.objects.create(
            city=self.london, name="Calendar", slug="calendar", connector="ical",
            url="https://example.com/calendar.ics",
        )
        self.payload = EventPayload(
            external_id="provider-1", title="Community gathering", url="https://example.com/event",
            start_date=date(2027, 6, 1), start_time=time(18), venue_name="Town Hall",
        )

    def sync(self, payload=None):
        record = payload or self.payload
        return import_source(
            self.source, date(2027, 6, 1), date(2027, 6, 30),
            connector=lambda *_: [record],
        )

    def test_city_can_change_before_a_source_has_imported_listings(self):
        self.source.city = self.paris
        self.source.full_clean()
        self.source.save()
        self.assertEqual(Source.objects.get(pk=self.source.pk).city_id, self.paris.pk)

    def test_model_validation_rejects_city_change_after_import(self):
        self.assertEqual(self.sync().status, ImportRun.Status.SUCCESS)
        self.source.city = self.paris
        with self.assertRaises(ValidationError) as error:
            self.source.full_clean()
        self.assertIn("city", error.exception.message_dict)
        self.assertEqual(Source.objects.get(pk=self.source.pk).city_id, self.london.pk)

    def test_imported_source_still_allows_ordinary_configuration_changes(self):
        self.assertEqual(self.sync().status, ImportRun.Status.SUCCESS)
        self.source.name = "Renamed calendar"
        self.source.interval_minutes = 120
        self.source.full_clean()
        self.source.save()
        self.assertEqual(Source.objects.get(pk=self.source.pk).name, "Renamed calendar")

    def test_admin_makes_city_readonly_only_after_import(self):
        model_admin = admin.site._registry[Source]
        request = RequestFactory().get("/admin/events/source/")
        request.user = self.user
        self.assertNotIn("city", model_admin.get_readonly_fields(request))
        self.assertIn("city", model_admin.get_form(request, self.source).base_fields)
        self.assertEqual(self.sync().status, ImportRun.Status.SUCCESS)
        self.assertIn("city", model_admin.get_readonly_fields(request, self.source))
        self.assertNotIn("city", model_admin.get_form(request, self.source).base_fields)

    def assert_corrupt_source_fails_safely(self, payload):
        self.assertEqual(self.sync().status, ImportRun.Status.SUCCESS)
        listing = SourceListing.objects.get(source=self.source)
        original_seen = listing.last_seen_at
        original_success = self.source.last_success_at
        original_event_id = listing.event_id
        # Simulate a bulk edit that deliberately bypassed model/admin validation.
        Source.objects.filter(pk=self.source.pk).update(city=self.paris)
        self.source.refresh_from_db()

        run = self.sync(payload)

        self.assertEqual(run.status, ImportRun.Status.FAILED)
        self.assertIn("city", run.error.lower())
        self.assertEqual(run.created_count, 0)
        self.assertEqual(run.updated_count, 0)
        self.assertEqual(Event.objects.count(), 1)
        listing.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(listing.event_id, original_event_id)
        self.assertEqual(listing.event.city_id, self.london.pk)
        self.assertEqual(listing.event.title, self.payload.title)
        self.assertEqual(listing.last_seen_at, original_seen)
        self.assertEqual(self.source.last_success_at, original_success)

    def test_unchanged_payload_cannot_bypass_city_integrity_check(self):
        self.assert_corrupt_source_fails_safely(self.payload)

    def test_changed_payload_cannot_move_existing_event_to_another_city(self):
        self.assert_corrupt_source_fails_safely(replace(self.payload, title="Changed title"))
