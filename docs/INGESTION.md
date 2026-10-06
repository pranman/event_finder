# Import behaviour

Connectors emit normalized `EventPayload` values with dates/times in the source
city's IANA time zone. A missing time is a date-only record, not midnight. Missing
prices remain unknown; the catalogue does not infer that an event is free from
its description. A source instance belongs to one city. Add another instance to
use the same connector in another city.

`sync_events` fetches outside the database transaction, validates a whole source
batch, then commits it atomically. Every attempt records an `ImportRun`. Failure
rolls back that source's writes, preserves its last success time, and makes the
command exit nonzero after attempting the remaining selected sources. A local
OS file lock prevents simultaneous command writers against the same SQLite DB.

Provider identity is `(source, external_id)`. Titles and dates can change without
creating a new record. Cross-source matches require the same city, exact title,
known venue and session time. A conflicting reschedule splits the affected
listing instead of moving another provider's event. Publication decisions made
in admin survive imports; imported content otherwise follows the provider.

## Rescheduling and bounded reconciliation (#9)

Before fetching, the importer supplies connectors with provider IDs of stored
records overlapping the requested discovery window (maximum 10,000). A returned
known event can update its stored date even when it has moved outside that window.
New events outside the discovery window are excluded.

JSON-LD preserves known IDs from fetched event pages. iCalendar preserves known
UIDs and recurrence identities, including explicit moved or cancelled overrides;
master metadata is inherited when an override omits it. Recurrence expansion and
page/detail counts are bounded. The regression suite covers moves before/after
the window, recurrence moves, explicit cancellation and unknown out-of-range IDs.

An update can only be reconciled if the provider includes it in the fetched
pages/feed. Absence never implies cancellation: pagination changes, retention
limits and outages are not reliable cancellation signals. Revisit source URLs
and inspect per-listing last-seen timestamps when investigating old records.

## Source city changes (#8)

Once a source has imported listings, its city is fixed in admin and model
validation. To cover another city, create a new source instance with a distinct
slug and that city's configuration. Do not update Source.city through bulk ORM
operations. The importer checks provenance even for unchanged payloads and
records a failure if a bulk edit has made a listing inconsistent. Restore the
original source city before importing again.
