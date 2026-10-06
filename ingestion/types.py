"""The normalized boundary between provider connectors and database writes."""
from dataclasses import asdict, dataclass
from datetime import date, time
from decimal import Decimal
import hashlib
import json
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class EventPayload:
    external_id: str
    title: str
    url: str
    start_date: date
    start_time: time | None = None
    end_date: date | None = None
    end_time: time | None = None
    description: str = ""
    venue_name: str = ""
    address: str = ""
    price_status: str = "unknown"
    price_amount: Decimal | None = None
    currency: str = ""
    status: str = "scheduled"
    categories: tuple[str, ...] = ()
    image_url: str = ""

    def validate(self):
        """Reject incomplete records before a source batch changes the catalogue."""
        if not self.external_id.strip() or len(self.external_id) > 500:
            raise ValueError("A provider identifier of at most 500 characters is required.")
        if not self.title.strip() or len(self.title) > 500:
            raise ValueError("An event title of at most 500 characters is required.")
        for value in (self.url, self.image_url):
            if value and (urlsplit(value).scheme not in {"http", "https"} or not urlsplit(value).hostname):
                raise ValueError("Event links must be absolute HTTP(S) URLs.")
        if not self.url or not isinstance(self.start_date, date):
            raise ValueError("An event date and original URL are required.")
        if self.price_status not in {"free", "paid", "unknown"}:
            raise ValueError("Unknown price status.")
        if self.status not in {"scheduled", "cancelled", "postponed"}:
            raise ValueError("Unknown event status.")
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("Event ends before its start date.")
        for value in (self.start_time, self.end_time):
            if value is not None and (not isinstance(value, time) or value.tzinfo is not None):
                raise ValueError("Times must be expressed in the source city's local time.")
        if self.price_amount is not None:
            if not self.price_amount.is_finite() or self.price_amount < 0:
                raise ValueError("Price must be finite and nonnegative.")
            if not self.currency:
                raise ValueError("A currency is required with a price amount.")
        return self

    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()

    def event_fields(self):
        values = asdict(self)
        values.pop("external_id")
        values.pop("categories")
        return values
