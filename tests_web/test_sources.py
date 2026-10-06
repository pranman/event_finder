import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from events.models import City, Source


class SourceSeedTests(TestCase):
    def setUp(self):
        self.city = City.objects.create(name="Example", slug="example", country_code="GB", timezone="Europe/London")
        self.row = {"name": "Feed", "slug": "feed", "city": "example", "connector": "ical", "url": "https://example.org/events.ics"}

    def load(self, rows):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(json.dumps({"sources": rows}))
            call_command("seed_sources", file=path, stdout=StringIO())

    def test_repeat_seed_keeps_one_source_and_supports_other_cities(self):
        self.load([self.row])
        self.load([dict(self.row, name="Renamed feed")])
        self.assertEqual(Source.objects.count(), 1)
        self.assertEqual(Source.objects.get().name, "Renamed feed")
        self.assertEqual(Source.objects.get().city, self.city)

    def test_invalid_connector_rolls_back_whole_file(self):
        with self.assertRaises(CommandError):
            self.load([self.row, dict(self.row, slug="bad", connector="unknown")])
        self.assertFalse(Source.objects.exists())

    def test_missing_city_is_actionable(self):
        with self.assertRaisesMessage(CommandError, "Seed the configured city"):
            self.load([dict(self.row, city="absent")])

    def test_duplicate_slugs_are_rejected(self):
        with self.assertRaisesMessage(CommandError, "distinct nonempty slugs"):
            self.load([self.row, self.row])
        self.assertFalse(Source.objects.exists())
