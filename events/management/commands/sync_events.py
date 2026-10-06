"""Refresh configured sources independently from web requests."""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from events.models import City, Source
from ingestion.locking import import_lock
from ingestion.service import import_source


class Command(BaseCommand):
    help = "Import configured event sources; without --city, sync all active cities."

    def add_arguments(self, parser):
        parser.add_argument("--city", help="Configured city slug.")
        parser.add_argument("--source", help="Configured source slug.")
        parser.add_argument("--days", type=int, default=60, help="Days ahead to fetch (1–365).")
        parser.add_argument("--from", dest="date_from", type=date.fromisoformat)
        parser.add_argument("--due", action="store_true", help="Only refresh sources whose interval has elapsed.")

    def handle(self, *args, **options):
        if not 1 <= options["days"] <= 365:
            raise CommandError("--days must be between 1 and 365.")
        sources = Source.objects.filter(enabled=True, city__is_active=True).select_related("city")
        if options["city"]:
            if not City.objects.filter(slug=options["city"], is_active=True).exists():
                raise CommandError("No active city matches --city.")
            sources = sources.filter(city__slug=options["city"])
        if options["source"]:
            sources = sources.filter(slug=options["source"])
            if not sources.exists():
                raise CommandError("No enabled source matches the selected city/source.")
        failures = 0
        attempted = 0
        with import_lock():
            for source in sources:
                now = timezone.now()
                if options["due"] and source.last_success_at and (
                    now - source.last_success_at < timedelta(minutes=source.interval_minutes)
                ):
                    continue
                start = options["date_from"] or timezone.localdate(now, source.city.tzinfo)
                end = start + timedelta(days=options["days"] - 1)
                run = import_source(source, start, end)
                attempted += 1
                self.stdout.write(
                    f"{source.slug}: {run.status}; {run.received_count} records, "
                    f"{run.created_count} created, {run.updated_count} updated"
                )
                if run.status == "failed":
                    failures += 1
                    self.stderr.write(run.error)
        if failures:
            raise CommandError(f"{failures} of {attempted} source imports failed; inspect Import runs in admin.")
        self.stdout.write(self.style.SUCCESS(f"Completed {attempted} source imports."))
