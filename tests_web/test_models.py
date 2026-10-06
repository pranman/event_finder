import json
from datetime import date, time, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from events.models import Category, City, Event, ImportRun, Source, SourceListing


class CatalogueModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.london = City.objects.create(
            name="London", slug="london", country_code="GB", timezone="Europe/London", currency="GBP",
        )
        cls.toronto = City.objects.create(
            name="Toronto", slug="toronto", country_code="CA", timezone="America/Toronto", currency="CAD",
        )
        cls.source = Source.objects.create(
            city=cls.london, name="Calendar", slug="calendar", connector="ical",
            url="https://example.com/events.ics",
        )

    def make_event(self, **kwargs):
        return Event.objects.create(**{
            "city": self.london, "title": "Community gathering", "start_date": date(2027, 6, 1), **kwargs,
        })

    def test_city_rejects_invalid_timezone(self):
        self.london.timezone = "Moon/SeaOfTranquillity"
        with self.assertRaises(ValidationError) as error:
            self.london.full_clean()
        self.assertIn("timezone", error.exception.message_dict)

    def test_event_identity_includes_neither_title_nor_city_assumptions(self):
        first = self.make_event()
        same_city = self.make_event(venue_name="Another venue")
        other_city = self.make_event(city=self.toronto)
        self.assertEqual(Event.objects.count(), 3)
        self.assertEqual(len({first.public_id, same_city.public_id, other_city.public_id}), 3)

    def test_date_only_event_does_not_invent_a_start_time(self):
        event = self.make_event()
        self.assertIsNone(event.starts_at)
        self.assertIsNone(event.ends_at)

    def test_start_time_uses_city_timezone_and_daylight_saving(self):
        event = self.make_event(start_time=time(18, 0))
        self.assertEqual(event.starts_at.utcoffset(), timedelta(hours=1))
        event.start_date = date(2027, 1, 1)
        self.assertEqual(event.starts_at.utcoffset(), timedelta(0))
        event.city = self.toronto
        self.assertEqual(event.starts_at.utcoffset(), timedelta(hours=-5))

    def test_date_order_is_enforced_by_validation_and_database(self):
        event = self.make_event()
        event.end_date = date(2027, 5, 31)
        with self.assertRaises(ValidationError):
            event.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            event.save()

    def test_same_day_end_time_cannot_precede_start(self):
        event = self.make_event(start_time=time(18), end_time=time(17))
        with self.assertRaises(ValidationError) as error:
            event.full_clean()
        self.assertIn("end_time", error.exception.message_dict)
        event.end_date = date(2027, 6, 2)
        event.full_clean()

    def test_prices_require_currency_and_cannot_be_negative(self):
        event = self.make_event(price_amount=Decimal("10"), price_status="paid")
        with self.assertRaises(ValidationError) as error:
            event.full_clean()
        self.assertIn("currency", error.exception.message_dict)
        event.currency = "GBP"
        event.full_clean()
        event.price_amount = Decimal("-1")
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_free_events_cannot_have_nonzero_price(self):
        event = self.make_event(price_status="free", price_amount=Decimal("5"), currency="GBP")
        with self.assertRaises(ValidationError) as error:
            event.full_clean()
        self.assertIn("price_amount", error.exception.message_dict)

    def test_public_urls_accept_only_http_and_https(self):
        event = self.make_event(url="ftp://example.com/tickets")
        with self.assertRaises(ValidationError) as error:
            event.full_clean()
        self.assertIn("url", error.exception.message_dict)
        event.url = "https://example.com/tickets"
        event.full_clean()

    def test_source_config_must_be_object_and_interval_positive(self):
        for invalid in ([], ["calendar"], "text", 12):
            self.source.config = invalid
            with self.subTest(config=invalid), self.assertRaises(ValidationError):
                self.source.full_clean()
        self.source.config = {}
        self.source.interval_minutes = 0
        with self.assertRaises(ValidationError):
            self.source.full_clean()

    def test_source_external_identity_is_unique_and_scoped_to_source(self):
        first = self.make_event()
        second = self.make_event()
        SourceListing.objects.create(source=self.source, event=first, external_id="event-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceListing.objects.create(source=self.source, event=second, external_id="event-1")
        other = Source.objects.create(
            city=self.london, name="Second calendar", slug="second", connector="ical",
            url="https://example.com/second.ics",
        )
        SourceListing.objects.create(source=other, event=first, external_id="event-1")
        self.assertEqual(first.source_listings.count(), 2)

    def test_source_listing_cannot_link_another_city(self):
        listing = SourceListing(source=self.source, event=self.make_event(city=self.toronto), external_id="1")
        with self.assertRaises(ValidationError) as error:
            listing.full_clean()
        self.assertIn("event", error.exception.message_dict)

    def test_catalogue_and_diagnostics_are_registered_in_admin(self):
        for model in (City, Category, Event, Source, SourceListing, ImportRun):
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))


class SeedCitiesTests(TestCase):
    def write_configuration(self, folder, data):
        path = Path(folder) / "cities.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_seed_is_generic_idempotent_and_updates_existing_rows(self):
        with TemporaryDirectory() as folder:
            data = {
                "cities": [{
                    "name": "Berlin", "slug": "berlin", "country_code": "DE",
                    "timezone": "Europe/Berlin", "currency": "EUR",
                }],
                "categories": [{"name": "Arts", "slug": "arts"}],
            }
            path = self.write_configuration(folder, data)
            call_command("seed_cities", file=path, stdout=StringIO())
            call_command("seed_cities", file=path, stdout=StringIO())
            self.assertEqual(City.objects.count(), 1)
            self.assertEqual(Category.objects.count(), 1)
            data["cities"][0]["is_active"] = False
            path = self.write_configuration(folder, data)
            call_command("seed_cities", file=path, stdout=StringIO())
            self.assertFalse(City.objects.get(slug="berlin").is_active)

    def test_invalid_configuration_rolls_back_entire_seed(self):
        with TemporaryDirectory() as folder:
            path = self.write_configuration(folder, {
                "cities": [
                    {"name": "Paris", "slug": "paris", "country_code": "FR", "timezone": "Europe/Paris"},
                    {"name": "Broken", "slug": "broken", "country_code": "GB", "timezone": "Invalid/Zone"},
                ],
            })
            with self.assertRaises(CommandError):
                call_command("seed_cities", file=path, stdout=StringIO())
            self.assertFalse(City.objects.exists())

    def test_rejects_duplicate_slugs_in_one_configuration(self):
        with TemporaryDirectory() as folder:
            path = self.write_configuration(folder, {"categories": [
                {"name": "Music", "slug": "music"}, {"name": "Other music", "slug": "music"},
            ]})
            with self.assertRaises(CommandError):
                call_command("seed_cities", file=path, stdout=StringIO())
            self.assertFalse(Category.objects.exists())
