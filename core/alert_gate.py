"""
core/alert_gate.py — Combines Worth + Urgency + state + daily cap
to decide whether an event should trigger an instant alert.

All five conditions must hold for an alert to fire:
  1. worth  >= worth_threshold   (event is relevant to the user)
  2. urgency >= urgency_threshold (event will fill fast / is one-time)
  3. Event is new (first_seen == this run) and not recurring
  4. No alert was previously sent for this event (alerted_at is null)
  5. Daily alert cap not yet reached
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from models import Event

if TYPE_CHECKING:
    from core.state import StateStore

logger = logging.getLogger(__name__)


class AlertGate:
    """
    Gates instant alerts using Worth, Urgency, state, and a daily cap.

    Parameters
    ----------
    config:
        Parsed config.yaml dict.
    state:
        Loaded StateStore instance.
    """

    def __init__(self, config: dict[str, Any], state: StateStore) -> None:
        alerts_cfg = config.get("alerts", {})
        self._worth_threshold: float = alerts_cfg.get("worth_threshold", 0.55)
        self._urgency_threshold: float = alerts_cfg.get("urgency_threshold", 0.6)
        self._max_per_day: int = alerts_cfg.get("max_alerts_per_day", 2)
        self._state = state
        self._alerts_sent_today: int = 0

    def should_alert(self, event: Event, worth: float, urgency: float) -> bool:
        """
        Return True if an instant alert should fire for *event*.

        Logs the reason for rejection so the caller can audit decisions.
        """
        if worth < self._worth_threshold:
            logger.debug(
                "Alert skipped [low worth %.2f < %.2f]: %s",
                worth, self._worth_threshold, event.title[:50],
            )
            return False
        if urgency < self._urgency_threshold:
            logger.debug(
                "Alert skipped [low urgency %.2f < %.2f]: %s",
                urgency, self._urgency_threshold, event.title[:50],
            )
            return False
        if not self._state.is_new(event.id):
            logger.debug("Alert skipped [not new this run]: %s", event.title[:50])
            return False
        if self._state.is_recurring(event.id):
            logger.debug("Alert skipped [recurring]: %s", event.title[:50])
            return False
        if self._state.has_been_alerted(event.id):
            logger.debug("Alert skipped [already alerted]: %s", event.title[:50])
            return False
        if self._alerts_sent_today >= self._max_per_day:
            logger.info(
                "Alert skipped [daily cap %d reached]: %s",
                self._max_per_day, event.title[:50],
            )
            return False
        return True

    def record_alert(self, event: Event) -> None:
        """Call after sending an alert to update state and the daily counter."""
        self._state.mark_alerted(event.id)
        self._alerts_sent_today += 1
        logger.info(
            "Instant alert recorded (%d/%d today): %s",
            self._alerts_sent_today, self._max_per_day, event.title[:50],
        )

    @property
    def alerts_sent_today(self) -> int:
        return self._alerts_sent_today

    @property
    def cap_reached(self) -> bool:
        return self._alerts_sent_today >= self._max_per_day
