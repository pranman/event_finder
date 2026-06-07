"""
core/state.py — Persistent store of seen events.

Tracks which events we've already seen, how many times,
whether they're recurring, and whether we've sent an alert.
Backed by a JSON file; no server required.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = "./state/seen_events.json"


class EventRecord:
    __slots__ = ("event_key", "first_seen", "times_seen", "observed_dates", "alerted_at")

    def __init__(
        self,
        event_key: str,
        first_seen: str,
        times_seen: int = 1,
        observed_dates: list[str] | None = None,
        alerted_at: str | None = None,
    ) -> None:
        self.event_key = event_key
        self.first_seen = first_seen
        self.times_seen = times_seen
        self.observed_dates: list[str] = observed_dates or []
        self.alerted_at = alerted_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "first_seen": self.first_seen,
            "times_seen": self.times_seen,
            "observed_dates": self.observed_dates,
            "alerted_at": self.alerted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EventRecord:
        return cls(
            event_key=d["event_key"],
            first_seen=d["first_seen"],
            times_seen=d.get("times_seen", 1),
            observed_dates=d.get("observed_dates", []),
            alerted_at=d.get("alerted_at"),
        )


class StateStore:
    """
    Persistent store for tracking seen events across pipeline runs.

    Call :meth:`load` once at startup and :meth:`save` before exit.
    During a run, call :meth:`record_event` for every event observed.
    """

    # Minimum sightings to classify an event as recurring
    _RECURRING_THRESHOLD = 3

    def __init__(self, state_path: str | Path = _DEFAULT_STATE_PATH) -> None:
        self._path = Path(state_path)
        self._records: dict[str, EventRecord] = {}
        self._run_ts = datetime.now().isoformat()
        self._new_keys: set[str] = set()

    # ── persistence ───────────────────────────────────────────────

    def load(self) -> None:
        """Load state from disk. No-op if the file doesn't exist yet."""
        if not self._path.exists():
            logger.debug("State file not found — starting fresh: %s", self._path)
            return
        try:
            with open(self._path) as f:
                data: list[dict[str, Any]] = json.load(f)
            self._records = {r["event_key"]: EventRecord.from_dict(r) for r in data}
            logger.info("State loaded: %d records from %s", len(self._records), self._path)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("State file corrupt — starting fresh: %s", exc)
            self._records = {}

    def save(self) -> None:
        """Persist state to disk, creating parent directories as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump([r.to_dict() for r in self._records.values()], f, indent=2)
        logger.debug("State saved: %d records to %s", len(self._records), self._path)

    # ── mutation ──────────────────────────────────────────────────

    def record_event(self, event_key: str, event_date: date) -> None:
        """
        Record a sighting of an event in this run.

        Sets ``first_seen`` when encountering a new key; increments
        ``times_seen`` and appends the date for returning events.
        """
        date_str = event_date.isoformat()
        if event_key in self._records:
            rec = self._records[event_key]
            rec.times_seen += 1
            if date_str not in rec.observed_dates:
                rec.observed_dates.append(date_str)
        else:
            self._records[event_key] = EventRecord(
                event_key=event_key,
                first_seen=self._run_ts,
                times_seen=1,
                observed_dates=[date_str],
            )
            self._new_keys.add(event_key)

    def mark_alerted(self, event_key: str) -> None:
        """Record that an instant alert was sent for this event."""
        rec = self._records.get(event_key)
        if rec is None:
            logger.warning("mark_alerted called on unknown event_key: %s", event_key)
            return
        rec.alerted_at = datetime.now().isoformat()

    # ── queries ───────────────────────────────────────────────────

    def is_new(self, event_key: str) -> bool:
        """True if this key was first seen in the current run."""
        return event_key in self._new_keys

    def is_recurring(self, event_key: str) -> bool:
        """
        True if the event appears on a recurring cadence.

        Requires times_seen ≥ threshold AND at least two distinct
        observed dates (guards against one-day multi-scraper noise).
        """
        rec = self._records.get(event_key)
        if rec is None:
            return False
        if rec.times_seen < self._RECURRING_THRESHOLD:
            return False
        return len(set(rec.observed_dates)) >= 2

    def has_been_alerted(self, event_key: str) -> bool:
        """True if an instant alert has already been sent for this event."""
        rec = self._records.get(event_key)
        return rec is not None and rec.alerted_at is not None
