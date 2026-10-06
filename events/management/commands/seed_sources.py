"""Load source instances from data; no provider or city is special-cased."""
import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from events.models import City, Source
from ingestion.connectors import CONNECTORS


class Command(BaseCommand):
    help = "Create or update configured sources from a JSON file."

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, default=Path(settings.BASE_DIR) / "data" / "sources.json")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            data = json.loads(Path(options["file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read source configuration: {exc}") from exc
        if not isinstance(data, dict) or set(data) != {"sources"} or not isinstance(data["sources"], list):
            raise CommandError("Expected a JSON object containing a sources array.")
        allowed = {"name", "slug", "city", "connector", "url", "config", "enabled", "interval_minutes"}
        seen = set()
        for row in data["sources"]:
            if not isinstance(row, dict) or set(row) - allowed:
                raise CommandError("Invalid source configuration fields.")
            slug = row.get("slug")
            if not isinstance(slug, str) or not slug or slug in seen:
                raise CommandError("Sources need distinct nonempty slugs.")
            seen.add(slug)
            try:
                city = City.objects.get(slug=row.get("city"))
            except City.DoesNotExist as exc:
                raise CommandError("Seed the configured city before adding its source.") from exc
            if row.get("connector") not in CONNECTORS:
                raise CommandError(f"Unknown connector for {slug}.")
            obj = Source.objects.filter(slug=slug).first() or Source(slug=slug)
            obj.city = city
            for name, value in row.items():
                if name != "city":
                    setattr(obj, name, value)
            try:
                obj.full_clean()
            except ValidationError as exc:
                raise CommandError(f"Invalid source {slug}: {exc}") from exc
            obj.save()
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(seen)} sources."))
