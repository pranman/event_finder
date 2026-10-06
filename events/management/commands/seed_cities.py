"""Load editable city/category configuration; nothing here assumes a location."""

import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from events.models import Category, City


class Command(BaseCommand):
    help = "Create or update cities and categories from a JSON configuration file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", type=Path, default=Path(settings.BASE_DIR) / "data" / "cities.json",
            help="JSON object containing cities and categories arrays (default: data/cities.json).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            data = json.loads(Path(options["file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read city configuration: {exc}") from exc
        if not isinstance(data, dict) or set(data) - {"cities", "categories"}:
            raise CommandError("Expected an object with cities and categories arrays.")
        totals = {"cities": 0, "categories": 0}
        allowed = {
            "cities": {"name", "slug", "country_code", "timezone", "currency", "is_active"},
            "categories": {"name", "slug"},
        }
        for key, model in (("cities", City), ("categories", Category)):
            rows = data.get(key, [])
            if not isinstance(rows, list):
                raise CommandError(f"{key} must be an array.")
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) - allowed[key]:
                    raise CommandError(f"Invalid {key} entry; allowed fields: {', '.join(sorted(allowed[key]))}.")
                slug = row.get("slug")
                if not isinstance(slug, str) or not slug:
                    raise CommandError(f"Each {key} entry needs a slug.")
                if slug in seen:
                    raise CommandError(f"Duplicate {key} slug: {slug}.")
                seen.add(slug)
                obj = model.objects.filter(slug=slug).first() or model(slug=slug)
                for field, value in row.items():
                    setattr(obj, field, value)
                try:
                    obj.full_clean()
                except ValidationError as exc:
                    raise CommandError(f"Invalid {key} entry {slug}: {exc}") from exc
                obj.save()
                totals[key] += 1
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {totals['cities']} cities and {totals['categories']} categories."
        ))
