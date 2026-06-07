"""
email_digest/renderer.py — Builds the HTML email body from a DigestSummary.

Uses Jinja2 to render template.html with the event data.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from models import Category, DigestSummary, Event

_TEMPLATE_DIR = Path(__file__).parent
_TEMPLATE_FILE = "template.html"
_ALERT_TEMPLATE_FILE = "alert_template.html"

# Category config matching config.yaml structure
_CATEGORY_CONFIG = {
    "tech":     {"emoji": "💻", "label": "Tech & Startups"},
    "concerts": {"emoji": "🎵", "label": "Concerts & Live Music"},
    "running":  {"emoji": "🏃", "label": "Running & Fitness"},
    "other":    {"emoji": "📅", "label": "Other Events"},
}


class EmailRenderer:
    """Renders a DigestSummary into an HTML email string."""

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )

    def render(self, digest: DigestSummary, user_email: str) -> str:
        """
        Render the Jinja2 template with digest data.

        Parameters
        ----------
        digest:
            The fully populated DigestSummary from the pipeline.
        user_email:
            Recipient email address (shown in footer unsubscribe link).

        Returns
        -------
        str
            Complete HTML email body, ready to send.
        """
        template = self._env.get_template(_TEMPLATE_FILE)

        # Build per-category event lists (keep top N, sorted by start_dt)
        events_by_category: dict[str, list[Event]] = {k: [] for k in _CATEGORY_CONFIG}
        for event in digest.all_events:
            for cat in event.categories:
                if cat.value in events_by_category:
                    events_by_category[cat.value].append(event)

        # Sort each category by start_dt, limit to 10
        for key in events_by_category:
            events_by_category[key] = sorted(
                events_by_category[key], key=lambda e: e.start_dt
            )[:10]

        # Build zone summary
        events_by_zone: dict[str, int] = {}
        for event in digest.all_events:
            zone = event.location_zone.value
            events_by_zone[zone] = events_by_zone.get(zone, 0) + 1

        # Human-readable date range
        date_range = (
            f"{digest.date_range_start.strftime('%b %-d')} – "
            f"{digest.date_range_end.strftime('%b %-d, %Y')}"
        )

        digest_label = digest.date_range_start.strftime("%A, %B %-d")

        return template.render(
            digest_label=digest_label,
            date_range=date_range,
            total_events=digest.total_events,
            top_picks=digest.top_picks[:5],
            events_by_category=events_by_category,
            events_by_zone={k: v for k, v in sorted(
                events_by_zone.items(), key=lambda x: -x[1]
            ) if k != "Unknown"},
            category_config=_CATEGORY_CONFIG,
            generated_at=digest.generated_at.strftime("%B %-d, %Y at %-I:%M %p"),
            user_email=user_email,
        )

    def render_alert(self, event: Event, user_email: str) -> str:
        """
        Render the instant-alert email for a single fast-filling event.

        Parameters
        ----------
        event:
            The event to alert on (must be fully scored/categorized).
        user_email:
            Recipient email address (shown in footer unsubscribe link).

        Returns
        -------
        str
            Complete HTML email body, ready to send.
        """
        template = self._env.get_template(_ALERT_TEMPLATE_FILE)
        return template.render(event=event, user_email=user_email)
