"""
core/urgency.py — Cheap urgency scorer (zero LLM calls).

Computes a score in [0.0, 1.0] that answers: "will I miss this event
if I wait for the Thursday digest?" High score means act now; low score
means Thursday is fine.

Signal weights
──────────────
  Urgency keywords in title / description  → +0.30
  Free + fitness / wellness category       → +0.20
  Source tagged fast_alert: true           → +0.40

Hard overrides (collapse score to 0.0)
───────────────────────────────────────
  Event not new in this run (already known by state)
  Event classified as recurring by state
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from models import Category, Event, EventSource

if TYPE_CHECKING:
    from core.state import StateStore

_DEFAULT_KEYWORDS: list[str] = [
    "limited",
    "limited spots",
    "spots left",
    "rsvp",
    "first come",
    "sign up",
    "register now",
    "filling up",
    "almost full",
]

_FITNESS_TERMS: frozenset[str] = frozenset(
    {"yoga", "wellness", "fitness", "workout", "run", "running", "crossfit", "spin", "pilates"}
)


def compute_urgency(
    event: Event,
    config: dict[str, Any],
    state: StateStore,
) -> float:
    """
    Return urgency score in [0.0, 1.0] for *event*.

    Parameters
    ----------
    event:
        A categorized Event (categories must be populated).
    config:
        Parsed config.yaml dict.
    state:
        Loaded StateStore — must have already called record_event() for this event.
    """
    # Hard overrides — recurring or already known events are never urgent
    if not state.is_new(event.id):
        return 0.0
    if state.is_recurring(event.id):
        return 0.0

    score = 0.0

    # Keyword signal
    text = f"{event.title} {event.description}".lower()
    keywords: list[str] = config.get("urgency_keywords", _DEFAULT_KEYWORDS)
    if any(kw.lower() in text for kw in keywords):
        score += 0.30

    # Free + fitness / wellness
    if event.is_free and _is_fitness_or_wellness(event):
        score += 0.20

    # Source pre-tagged fast_alert: true in config
    if _is_fast_alert_source(event, config):
        score += 0.40

    return min(1.0, score)


def _is_fitness_or_wellness(event: Event) -> bool:
    if Category.RUNNING in event.categories:
        return True
    text = f"{event.title} {event.venue_name}".lower()
    return any(term in text for term in _FITNESS_TERMS)


def _is_fast_alert_source(event: Event, config: dict[str, Any]) -> bool:
    """True if the event's Instagram account is tagged fast_alert: true."""
    if event.source != EventSource.INSTAGRAM:
        return False
    accounts: list[dict[str, Any]] = config.get("instagram_accounts", [])
    for acct in accounts:
        if acct.get("fast_alert") and acct.get("handle", "") in (event.url or ""):
            return True
    return False
