"""A source-independent catalogue with dates interpreted in each city's time zone."""

from datetime import datetime
from decimal import Decimal
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator, URLValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


http_url = URLValidator(schemes=["http", "https"])
currency_code = RegexValidator(r"^[A-Z]{3}$", "Use a three-letter uppercase currency code.")
country_code = RegexValidator(r"^[A-Z]{2}$", "Use a two-letter uppercase country code.")


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValidationError("Enter a valid IANA time zone, such as Europe/London.")


def validate_config(value):
    if not isinstance(value, dict):
        raise ValidationError("Source configuration must be a JSON object.")


class City(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160, unique=True)
    country_code = models.CharField(max_length=2, validators=[country_code])
    timezone = models.CharField(max_length=64, default="UTC", validators=[validate_timezone])
    currency = models.CharField(max_length=3, blank=True, default="", validators=[currency_code])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "country_code"]
        verbose_name_plural = "cities"

    def __str__(self):
        return f"{self.name}, {self.country_code}"

    @property
    def tzinfo(self):
        return ZoneInfo(self.timezone)


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=110, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Event(models.Model):
    class PriceStatus(models.TextChoices):
        FREE = "free", "Free"
        PAID = "paid", "Paid"
        UNKNOWN = "unknown", "Unknown"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        CANCELLED = "cancelled", "Cancelled"
        POSTPONED = "postponed", "Postponed"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    url = models.URLField(max_length=2000, blank=True, validators=[http_url])
    venue_name = models.CharField(max_length=300, blank=True)
    address = models.TextField(blank=True)
    start_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    price_status = models.CharField(max_length=10, choices=PriceStatus, default=PriceStatus.UNKNOWN)
    price_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(max_length=3, blank=True, default="", validators=[currency_code])
    status = models.CharField(max_length=12, choices=Status, default=Status.SCHEDULED)
    is_published = models.BooleanField(default=True)
    image_url = models.URLField(max_length=2000, blank=True, validators=[http_url])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    categories = models.ManyToManyField(Category, blank=True, related_name="events")

    class Meta:
        ordering = ["start_date", "start_time", "title", "pk"]
        indexes = [
            models.Index(fields=["city", "is_published", "start_date"], name="event_city_publish_date_idx"),
            models.Index(fields=["city", "status", "start_date"], name="event_city_status_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="event_end_date_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(price_amount__isnull=True) | Q(price_amount__gte=0),
                name="event_price_nonnegative",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def starts_at(self):
        """An aware local datetime, or None when the source only supplies a date."""
        if self.start_time is None or self.start_date is None:
            return None
        return datetime.combine(self.start_date, self.start_time, tzinfo=self.city.tzinfo)

    @property
    def ends_at(self):
        if self.end_time is None or self.start_date is None:
            return None
        return datetime.combine(self.end_date or self.start_date, self.end_time, tzinfo=self.city.tzinfo)

    def clean(self):
        super().clean()
        errors = {}
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "The end date cannot be before the start date."
        if (
            self.start_date and self.start_time and self.end_time
            and (self.end_date is None or self.end_date == self.start_date)
            and self.end_time < self.start_time
        ):
            errors["end_time"] = "The end time cannot be before the start time on the same date."
        if self.price_status == self.PriceStatus.FREE and self.price_amount not in (None, Decimal("0")):
            errors["price_amount"] = "A free event cannot have a nonzero price."
        if self.price_amount is not None and not self.currency:
            errors["currency"] = "Specify the currency when a price amount is provided."
        if errors:
            raise ValidationError(errors)


class Source(models.Model):
    class Connector(models.TextChoices):
        JSONLD = "jsonld", "JSON-LD event page"
        ICAL = "ical", "iCalendar feed"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=210, unique=True)
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="sources")
    connector = models.CharField(max_length=30, choices=Connector)
    url = models.URLField(max_length=2000, validators=[http_url])
    config = models.JSONField(default=dict, blank=True, validators=[validate_config])
    enabled = models.BooleanField(default=True)
    interval_minutes = models.PositiveIntegerField(default=360, validators=[MinValueValidator(1)])
    last_success_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["city__name", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(interval_minutes__gte=1), name="source_interval_positive"),
        ]

    def __str__(self):
        return f"{self.name} ({self.city.name})"

    def clean(self):
        super().clean()
        # JSONField treats an empty list as blank; it must still be an object.
        validate_config(self.config)
        if (
            self.pk
            and type(self).objects.filter(pk=self.pk).exclude(city_id=self.city_id).exists()
            and self.listings.exists()
        ):
            raise ValidationError({
                "city": "A source with imported listings cannot change city. Create a new source for the other city."
            })


class SourceListing(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="listings")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="source_listings")
    external_id = models.CharField(max_length=500)
    source_url = models.URLField(max_length=2000, blank=True, validators=[http_url])
    last_seen_at = models.DateTimeField(default=timezone.now)
    payload_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_source_external_id"),
        ]
        indexes = [models.Index(fields=["source", "last_seen_at"], name="listing_source_seen_idx")]

    def __str__(self):
        return f"{self.source.name}: {self.external_id}"

    def clean(self):
        super().clean()
        if self.source_id and self.event_id and self.source.city_id != self.event.city_id:
            raise ValidationError({"event": "The event must belong to the source's city."})


class ImportRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="import_runs")
    status = models.CharField(max_length=10, choices=Status, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    received_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]
        indexes = [models.Index(fields=["source", "-started_at"], name="import_source_started_idx")]

    def __str__(self):
        return f"{self.source.name}: {self.status} ({self.started_at})"
