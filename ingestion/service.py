"""Atomic, observable catalogue imports with stable provider identity."""
from datetime import date
import logging
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
import httpx

from events.models import Category, Event, ImportRun, SourceListing
from ingestion.types import EventPayload

logger = logging.getLogger(__name__)


def _matching_event(source, payload):
    # Never infer a cross-source match from a generic title and date alone.
    # Without a known venue AND session time, retain separate records.
    if not payload.venue_name.strip() or payload.start_time is None:
        return None
    return Event.objects.filter(
        city=source.city, title__iexact=payload.title.strip(),
        venue_name__iexact=payload.venue_name.strip(),
        start_date=payload.start_date, start_time=payload.start_time,
        source_listings__isnull=False,
    ).exclude(source_listings__source=source).order_by("pk").first()


def _persist(source, payload, observed_at):
    listing = SourceListing.objects.select_related("event").filter(
        source=source, external_id=payload.external_id,
    ).first()
    digest = payload.fingerprint()
    if listing and listing.payload_hash == digest:
        listing.last_seen_at = observed_at
        listing.save(update_fields=["last_seen_at"])
        return 0, 0

    event = listing.event if listing else _matching_event(source, payload)
    if listing and event.source_listings.exclude(pk=listing.pk).exists():
        identity = ("title", "venue_name", "start_date", "start_time")
        if any(getattr(event, key) != getattr(payload, key) for key in identity):
            # Conflicting provider updates must not move another source's session.
            event = None
    created = event is None
    if created:
        event = Event(city=source.city)
    # Preserve an editor's publication decision and all source provenance.
    for field, value in payload.event_fields().items():
        setattr(event, field, value)
    event.full_clean()
    event.save()
    category_slugs = {
        slugify(value) for value in (*source.config.get("categories", []), *payload.categories)
    }
    event.categories.add(*Category.objects.filter(slug__in=category_slugs))
    if listing is None:
        listing = SourceListing(source=source, event=event, external_id=payload.external_id)
    listing.event = event
    listing.source_url = payload.url
    listing.last_seen_at = observed_at
    listing.payload_hash = digest
    listing.full_clean()
    listing.save()
    return int(created), int(not created)


def import_source(source, date_from: date, date_to: date, *, connector=None):
    """Fetch outside the transaction; validate then atomically persist one source.

    A failure rolls back every event change, leaves last_success_at intact and
    records a failed ImportRun. A successful empty response never deletes events.
    """
    from ingestion.connectors import CONNECTORS

    run = ImportRun.objects.create(source=source)
    try:
        fetch = connector or CONNECTORS[source.connector]
        payloads = fetch(source, date_from, date_to)
        if len(payloads) > 10000:
            raise ValueError("A source returned more than 10,000 events; narrow its date window.")
        unique = {}
        for payload in payloads:
            if not isinstance(payload, EventPayload):
                raise ValueError("Connector returned an invalid event payload.")
            payload.validate()
            if payload.external_id in unique and unique[payload.external_id] != payload:
                raise ValueError("Connector returned conflicting records for one provider identifier.")
            if payload.start_date <= date_to and (payload.end_date or payload.start_date) >= date_from:
                unique[payload.external_id] = payload
        run.received_count = len(unique)
        observed_at = timezone.now()
        with transaction.atomic():
            for payload in unique.values():
                created, updated = _persist(source, payload, observed_at)
                run.created_count += created
                run.updated_count += updated
            source.last_success_at = observed_at
            source.save(update_fields=["last_success_at"])
            run.status = ImportRun.Status.SUCCESS
            run.finished_at = timezone.now()
            run.save()
    except Exception as exc:
        logger.warning("Import failed for source %s (%s)", source.slug, type(exc).__name__)
        run.status = ImportRun.Status.FAILED
        run.created_count = run.updated_count = 0
        run.finished_at = timezone.now()
        if isinstance(exc, httpx.HTTPStatusError):
            run.error = f"HTTP {exc.response.status_code} from {urlsplit(str(exc.request.url)).hostname}"
        elif isinstance(exc, httpx.RequestError):
            run.error = f"{type(exc).__name__}: could not fetch the configured source."
        else:
            run.error = f"{type(exc).__name__}: {exc}"[:1500]
        run.save()
    return run
