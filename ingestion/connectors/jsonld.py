"""Import schema.org Event data from a page or configured index/detail pages."""

from collections import Counter
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ingestion.types import EventPayload
from . import common


def event_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from event_nodes(item)
    elif isinstance(value, dict):
        kinds = value.get("@type", [])
        if isinstance(kinds, str):
            kinds = [kinds]
        if any(isinstance(kind, str) and kind.rsplit("/", 1)[-1].endswith("Event") for kind in kinds):
            yield value
        else:
            for item in value.values():
                if isinstance(item, (dict, list)):
                    yield from event_nodes(item)


def parse_date(value, timezone):
    if not isinstance(value, str) or not value:
        raise common.SourceFormatError("Event is missing its date")
    try:
        if len(value) == 10:
            return date.fromisoformat(value), None
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone)
        return moment.date(), moment.time().replace(tzinfo=None)
    except ValueError as exc:
        raise common.SourceFormatError(f"Invalid event date: {value!r}") from exc


def price(data):
    offers = data.get("offers", [])
    if isinstance(offers, dict):
        offers = [offers]
    if not isinstance(offers, list):
        offers = []
    if data.get("isAccessibleForFree") is True:
        return "free", None, ""
    amounts = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        raw = offer.get("price", offer.get("lowPrice"))
        try:
            amount = Decimal(str(raw))
            if amount.is_finite() and amount >= 0:
                amounts.append((amount, str(offer.get("priceCurrency", ""))))
        except InvalidOperation:
            continue
    if not amounts:
        return "unknown", None, ""
    amount, currency = min(amounts)
    return "free" if amount == 0 else "paid", amount if currency else None, currency.upper()[:3]


def parse_event(data, page_url, source):
    title = common.plain_text(data.get("name", ""))
    if not title:
        raise common.SourceFormatError("Event is missing its name")
    timezone = ZoneInfo(source.city.timezone)
    start_date, start_time = parse_date(data.get("startDate"), timezone)
    end_date, end_time = parse_date(data["endDate"], timezone) if data.get("endDate") else (None, None)
    url = common.http_url(data.get("url"), page_url) or page_url
    identifier = data.get("identifier") or data.get("@id") or url
    if isinstance(identifier, dict):
        identifier = identifier.get("value") or url
    location = data.get("location", {})
    if isinstance(location, list):
        location = next((item for item in location if isinstance(item, dict) and item.get("address")), {})
    location = location if isinstance(location, dict) else {"name": location}
    address = location.get("address", "")
    if isinstance(address, dict):
        address = ", ".join(common.plain_text(address.get(key, "")) for key in (
            "streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"
        ) if isinstance(address.get(key), str) and address.get(key))
    status = {"EventCancelled": "cancelled", "EventPostponed": "postponed"}.get(
        str(data.get("eventStatus", "")).rsplit("/", 1)[-1], "scheduled"
    )
    image = data.get("image", "")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url", "")
    price_status, price_amount, currency = price(data)
    return EventPayload(
        external_id=str(identifier), title=title, url=url,
        start_date=start_date, start_time=start_time, end_date=end_date, end_time=end_time,
        description=common.plain_text(data.get("description", "")),
        venue_name=common.plain_text(location.get("name", "")), address=common.plain_text(address),
        price_status=price_status, price_amount=price_amount, currency=currency, status=status,
        categories=common.categories(source.config), image_url=common.http_url(image, page_url),
    )


def parse_page(soup, page_url, source):
    results = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            # Some publishers emit literal line breaks inside description strings.
            # strict=False accepts those characters without evaluating any code.
            data = json.loads(script.string or script.get_text(), strict=False)
        except (ValueError, TypeError):
            continue
        results.extend(parse_event(node, page_url, source) for node in event_nodes(data))
    return results


def fetch(source, date_from, date_to):
    """Fetch a direct Event page, or pages selected from a configured index.

    Config: link_selector, next_page_selector, max_pages (1–10),
    max_details (1–200), max_events (1–5000), categories, allow_empty.
    Missing expected metadata fails the run unless allow_empty is explicit.
    """
    max_details = common.limit(source.config, "max_details", 50, 200)
    max_events = common.limit(source.config, "max_events", 1000, 5000)
    selector = source.config.get("link_selector")
    results = []
    visited = set()
    with common.client() as client:
        for page_url, soup in common.index_pages(client, source):
            results.extend(parse_page(soup, page_url, source))
            if selector:
                for url in common.detail_links(soup, page_url, selector):
                    if url in visited or len(visited) >= max_details:
                        continue
                    visited.add(url)
                    response = common.get(client, url)
                    details = parse_page(BeautifulSoup(response.text, "html.parser"), str(response.url), source)
                    if not details:
                        raise common.SourceFormatError(f"No schema.org Event metadata found at {url}")
                    results.extend(details)
            if len(results) > max_events:
                raise common.SourceFormatError("Source exceeds the configured event limit")
    if not results and not source.config.get("allow_empty", False):
        raise common.SourceFormatError("No schema.org Event metadata or matching event links found")
    # Deduplicate cards/graphs before identifying multiple occurrences on one URL.
    unique = {(event.external_id, event.start_date, event.start_time): event for event in results}
    counts = Counter(event.external_id for event in unique.values())
    events = []
    for event in unique.values():
        if counts[event.external_id] > 1:
            suffix = f"#{event.start_date.isoformat()}T{event.start_time.isoformat() if event.start_time else 'date'}"
            event = replace(event, external_id=event.external_id + suffix)
        if event.start_date <= date_to and (event.end_date or event.start_date) >= date_from:
            events.append(event)
    return events
