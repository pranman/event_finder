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


def parse_calendar(content, page_url, source, date_from, date_to):
    if b"BEGIN:VCALENDAR" not in content:
        raise common.SourceFormatError("Source did not return an iCalendar document")
    calendar = Calendar.from_ical(content)
    timezone = ZoneInfo(source.city.timezone)
    max_events = common.limit(source.config, "max_events", 1000, 5000)
    originals = calendar.walk("VEVENT")
    recurring = set()
    explicit_ends = set()
    for item in originals:
        uid = str(item.get("UID", ""))
        if not uid or "DTSTART" not in item:
            raise common.SourceFormatError("Calendar events require UID and DTSTART")
        if any(name in item for name in ("RRULE", "RDATE", "RECURRENCE-ID")):
            recurring.add(uid)
        if "DTEND" in item or "DURATION" in item:
            explicit_ends.add(uid)
    events = []
    # A generator caps even an unbounded SECONDLY recurrence without allocating
    # every occurrence in the date window at once.
    earliest = datetime.combine(date_from, time.min, tzinfo=timezone)
    query = recurring_ical_events.of(calendar)
    for item in query.after(earliest):
        start_date, start_time = local_parts(item.decoded("DTSTART"), timezone)
        if start_date > date_to:
            break
        if len(events) >= max_events:
            raise common.SourceFormatError("Calendar exceeds the configured event limit")
        uid = str(item["UID"])
        title = common.plain_text(str(item.get("SUMMARY", "")))
        if not title:
            raise common.SourceFormatError("Calendar event is missing its summary")
        external_id = uid
        if uid in recurring:
            occurrence = item.decoded("RECURRENCE-ID", item.decoded("DTSTART"))
            external_id += "#" + occurrence.isoformat()
        end_date = end_time = None
        if uid in explicit_ends and "DTEND" in item:
            end_date, end_time = local_parts(item.decoded("DTEND"), timezone)
            # RFC 5545 all-day DTEND is exclusive; the catalogue uses inclusive
            # end dates for city/date browsing.
            if start_time is None and end_time is None:
                end_date -= timedelta(days=1)
        if (end_date or start_date) < date_from:
            continue
        location = common.plain_text(str(item.get("LOCATION", "")))
        venue_name, separator, address = location.partition(",")
        events.append(EventPayload(
            external_id=external_id, title=title,
            url=common.http_url(str(item.get("URL", "")), page_url) or page_url,
            start_date=start_date, start_time=start_time, end_date=end_date, end_time=end_time,
            description=common.plain_text(str(item.get("DESCRIPTION", ""))),
            venue_name=venue_name, address=address.strip() if separator else "",
            status="cancelled" if str(item.get("STATUS", "")).upper() == "CANCELLED" else "scheduled",
            categories=common.categories(source.config),
        ))
    return events


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
