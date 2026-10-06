"""Import iCalendar feeds, including recurrence and individual event exports."""

from datetime import date, datetime, time, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from icalendar import Calendar
import recurring_ical_events

from ingestion.types import EventPayload
from . import common


def local_parts(value, timezone):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone)
        return value.date(), value.time().replace(tzinfo=None)
    if isinstance(value, date):
        return value, None
    raise common.SourceFormatError("Calendar event has an invalid date")


def provider_id(item, recurring):
    uid = str(item["UID"])
    if uid in recurring:
        occurrence = item.decoded("RECURRENCE-ID", item.decoded("DTSTART"))
        return uid + "#" + occurrence.isoformat()
    return uid


def event_payload(item, page_url, source, recurring, explicit_ends):
    timezone = ZoneInfo(source.city.timezone)
    start_date, start_time = local_parts(item.decoded("DTSTART"), timezone)
    title = common.plain_text(str(item.get("SUMMARY", "")))
    if not title:
        raise common.SourceFormatError("Calendar event is missing its summary")
    end_date = end_time = None
    if str(item["UID"]) in explicit_ends:
        end = item.decoded("DTEND", None)
        if end is None and "DURATION" in item:
            end = item.decoded("DTSTART") + item.decoded("DURATION")
        if end is not None:
            end_date, end_time = local_parts(end, timezone)
            # RFC 5545 all-day DTEND is exclusive; catalogue dates are inclusive.
            if start_time is None and end_time is None:
                end_date -= timedelta(days=1)
    location = common.plain_text(str(item.get("LOCATION", "")))
    venue_name, separator, address = location.partition(",")
    return EventPayload(
        external_id=provider_id(item, recurring), title=title,
        url=common.http_url(str(item.get("URL", "")), page_url) or page_url,
        start_date=start_date, start_time=start_time, end_date=end_date, end_time=end_time,
        description=common.plain_text(str(item.get("DESCRIPTION", ""))),
        venue_name=venue_name, address=address.strip() if separator else "",
        status="cancelled" if str(item.get("STATUS", "")).upper() == "CANCELLED" else "scheduled",
        categories=common.categories(source.config),
    )


def complete_override(item, master):
    """Combine an explicit recurrence update with its series metadata.

    A moved instance inherits the original duration, not the master's absolute
    end date. RECURRENCE-ID continues to identify its original scheduled slot.
    """
    if master is None or item is master:
        return item
    result = master.copy()
    duration = master.decoded("DURATION", None)
    if duration is None and "DTEND" in master:
        duration = master.decoded("DTEND") - master.decoded("DTSTART")
    for key in ("DTSTART", "DTEND", "DURATION", "RRULE", "RDATE", "EXDATE"):
        result.pop(key, None)
    result.update(item)
    if "DTEND" not in item and "DURATION" not in item and duration is not None:
        result.add("DURATION", duration)
    return result


def parse_calendar(content, page_url, source, date_from, date_to):
    if b"BEGIN:VCALENDAR" not in content:
        raise common.SourceFormatError("Source did not return an iCalendar document")
    calendar = Calendar.from_ical(content)
    timezone = ZoneInfo(source.city.timezone)
    max_events = common.limit(source.config, "max_events", 1000, 5000)
    originals = calendar.walk("VEVENT")
    if len(originals) > 10000:
        raise common.SourceFormatError("Calendar exceeds the 10,000 component limit")
    recurring = set()
    explicit_ends = set()
    masters = {}
    for item in originals:
        uid = str(item.get("UID", ""))
        if not uid:
            raise common.SourceFormatError("Calendar events require UID and DTSTART")
        if "DTSTART" not in item:
            if "RECURRENCE-ID" in item and str(item.get("STATUS", "")).upper() == "CANCELLED":
                # A cancellation can refer only to the original scheduled slot.
                item.add("DTSTART", item.decoded("RECURRENCE-ID"))
            else:
                raise common.SourceFormatError("Calendar events require UID and DTSTART")
        if "RECURRENCE-ID" not in item:
            masters[uid] = item
        if any(name in item for name in ("RRULE", "RDATE", "RECURRENCE-ID")):
            recurring.add(uid)
        if "DTEND" in item or "DURATION" in item:
            explicit_ends.add(uid)
    events = {}
    # Normal discovery stays bounded even for an infinite SECONDLY recurrence.
    earliest = datetime.combine(date_from, time.min, tzinfo=timezone)
    query = recurring_ical_events.of(calendar)
    for item in query.after(earliest):
        start_date, _ = local_parts(item.decoded("DTSTART"), timezone)
        if start_date > date_to:
            break
        complete = complete_override(item, masters.get(str(item["UID"])))
        event = event_payload(complete, page_url, source, recurring, explicit_ends)
        if (event.end_date or event.start_date) < date_from:
            continue
        events[event.external_id] = event
        if len(events) > max_events:
            raise common.SourceFormatError("Calendar exceeds the configured event limit")
    # A provider may move a known event earlier/later than the discovery window.
    # Reconcile only explicit records for known IDs; absence is never cancellation.
    known_ids = getattr(source, "known_external_ids", frozenset())
    for item in originals:
        if provider_id(item, recurring) not in known_ids:
            continue
        complete = complete_override(item, masters.get(str(item["UID"])))
        event = event_payload(complete, page_url, source, recurring, explicit_ends)
        events[event.external_id] = event
        if len(events) > max_events:
            raise common.SourceFormatError("Calendar exceeds the configured event limit")
    return list(events.values())


def fetch(source, date_from, date_to):
    """Import a feed URL, or discover individual exports from an HTML index.

    Set link_selector and calendar_suffix (e.g. 'event.ics') for an index.
    Without a suffix the matching links must point directly to .ics resources.
    Pagination and request/event limits use the same config as JSON-LD.
    """
    max_details = common.limit(source.config, "max_details", 50, 200)
    max_events = common.limit(source.config, "max_events", 1000, 5000)
    selector = source.config.get("link_selector")
    events = []
    with common.client() as client:
        if not selector:
            response = common.get(client, source.url)
            return parse_calendar(response.content, str(response.url), source, date_from, date_to)
        visited = set()
        for page_url, soup in common.index_pages(client, source):
            for url in common.detail_links(soup, page_url, selector):
                if url in visited or len(visited) >= max_details:
                    continue
                visited.add(url)
                suffix = source.config.get("calendar_suffix", "")
                calendar_url = common.same_host_url(urljoin(url.rstrip("/") + "/", suffix), url) if suffix else url
                if not calendar_url:
                    raise common.SourceFormatError("calendar_suffix must keep exports on the source host")
                response = common.get(client, calendar_url)
                events.extend(parse_calendar(response.content, url, source, date_from, date_to))
                if len(events) > max_events:
                    raise common.SourceFormatError("Source exceeds the configured event limit")
        if not visited and not source.config.get("allow_empty", False):
            raise common.SourceFormatError("No matching calendar links found")
    return list({event.external_id: event for event in events}.values())
