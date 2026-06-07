"""
core/llm_extractor.py — LLM-powered structured event extraction.

Takes raw unstructured text (e.g. Reddit posts, BlogTO paragraphs) and
uses the OpenAI API to extract structured event fields: title, date,
time, venue, location, price.

This is the "good prompts" approach — we send a carefully crafted
prompt and parse the JSON response back into Event objects.

Requires: OPENAI_API_KEY in .env
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from models import Event, EventSource

logger = logging.getLogger(__name__)

# ── Extraction prompt ─────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are an event extraction assistant for a Toronto event digest.

From the following raw text, extract ALL distinct upcoming events that are:
- Located in Toronto, Ontario, Canada
- Happening in the near future

For each event, return a JSON array where each element has these fields:
{{
  "title": "Event name",
  "description": "Brief 1-2 sentence description",
  "date": "YYYY-MM-DD",
  "time": "HH:MM" or null,
  "venue_name": "Venue or location name" or null,
  "address": "Street address" or null,
  "price": "Free" or "$X" or "Unknown",
  "url": "Event URL if mentioned" or null,
  "category_hint": "tech" | "concerts" | "running" | "other"
}}

Rules:
- Only extract events with a clearly identifiable title and date
- If no date is found for an event, skip it
- If multiple dates are mentioned for the same event, create one entry per date
- Interpret relative dates (e.g. "this Saturday") relative to today: {today}
- Return ONLY the JSON array, no markdown, no extra text

Raw text to extract from:
---
{raw_text}
---
"""


_EVENT_SIGNAL_KEYWORDS: frozenset[str] = frozenset({
    # time markers
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "today", "tomorrow", "tonight", "this weekend", "next week",
    "pm", "am", "doors", "7pm", "8pm", "6pm",
    # event vocab
    "event", "events", "festival", "concert", "show", "workshop",
    "meetup", "conference", "hackathon", "networking", "panel",
    "rsvp", "ticket", "tickets", "free", "register", "registration",
    "venue", "location", "hosted by", "join us",
})


def _text_looks_like_events(text: str) -> bool:
    """
    Return True if *text* contains enough signal to be worth an LLM call.
    Checks for a minimum of 2 event-related keywords (case-insensitive).
    """
    lower = text.lower()
    hits = sum(1 for kw in _EVENT_SIGNAL_KEYWORDS if kw in lower)
    return hits >= 2


class LLMExtractor:
    """
    Uses OpenAI to extract structured events from raw text.

    Parameters
    ----------
    model_name:
        OpenAI model to use. Defaults to ``gpt-5.4-mini`` — the current
        cost-efficient flagship (replaces the retired gpt-4o-mini).
        Use ``gpt-5.4`` for maximum accuracy on dense or complex listings.
    """

    def __init__(self, model_name: str = "gpt-5.4-mini") -> None:
        self._model_name = model_name
        self._client: Any = None  # lazy-init on first use

    def _get_client(self) -> Any:
        """Lazy-initialise the OpenAI client."""
        if self._client is not None:
            return self._client

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to .env to enable LLM extraction."
            )

        from openai import OpenAI  # noqa: PLC0415
        self._client = OpenAI(api_key=api_key)
        return self._client

    async def extract_events(
        self,
        raw_text: str,
        source: EventSource = EventSource.REDDIT,
        source_url: str = "",
        today: datetime | None = None,
    ) -> list[Event]:
        """
        Send ``raw_text`` to OpenAI and return extracted Event objects.

        Parameters
        ----------
        raw_text: str
            Unstructured text to extract events from.
        source:
            Which scraper produced this text (used for attribution).
        source_url:
            URL of the original page (used as fallback event URL).
        today:
            Date to use for resolving relative date expressions.
            Defaults to current date.
        """
        if not raw_text.strip():
            return []

        # Cheap keyword pre-filter: skip LLM if the text is unlikely to contain events.
        # This is the biggest lever for reducing OpenAI spend on the weekly path.
        if not _text_looks_like_events(raw_text):
            logger.debug("LLM skipped (no event keywords in text): %.60s…", raw_text)
            return []

        today_dt = today or datetime.now()
        prompt = _EXTRACTION_PROMPT.format(
            raw_text=raw_text[:4000],  # stay within context window
            today=today_dt.strftime("%Y-%m-%d (%A)"),
        )

        try:
            client = self._get_client()

            # openai is sync; run in executor to avoid blocking asyncio
            import asyncio  # noqa: PLC0415

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=self._model_name,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a Toronto event extraction assistant. Always respond with valid JSON only.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    # Strict Structured Outputs — guarantees valid JSON schema
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "events",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "events": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "title":       {"type": "string"},
                                                "description": {"type": "string"},
                                                "date":        {"type": "string"},
                                                "time":        {"type": ["string", "null"]},
                                                "venue_name":  {"type": ["string", "null"]},
                                                "address":     {"type": ["string", "null"]},
                                                "price":       {"type": "string"},
                                                "url":         {"type": ["string", "null"]},
                                                "category_hint": {
                                                    "type": "string",
                                                    "enum": ["tech", "concerts", "running", "other"],
                                                },
                                            },
                                            "required": ["title", "description", "date", "time",
                                                         "venue_name", "address", "price", "url",
                                                         "category_hint"],
                                            "additionalProperties": False,
                                        },
                                    }
                                },
                                "required": ["events"],
                                "additionalProperties": False,
                            },
                        },
                    },
                ),
            )
            raw_json = response.choices[0].message.content.strip()

            # Strict schema wraps output as {"events": [...]}
            payload: dict[str, Any] = json.loads(raw_json)
            extracted: list[dict[str, Any]] = payload.get("events", [])
            return self._dicts_to_events(extracted, source, source_url)

        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON — skipping extraction")
            return []
        except RuntimeError as exc:
            logger.warning("LLM extraction skipped: %s", exc)
            return []
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected LLM extraction error")
            return []

    # ── private ───────────────────────────────────────────────────────

    def _dicts_to_events(
        self,
        extracted: list[dict[str, Any]],
        source: EventSource,
        source_url: str,
    ) -> list[Event]:
        events: list[Event] = []
        for item in extracted:
            try:
                date_str = item.get("date", "")
                time_str = item.get("time") or "00:00"
                dt_str = f"{date_str}T{time_str}:00"
                start_dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)

                title = (item.get("title") or "").strip()
                if not title:
                    continue

                events.append(
                    Event(
                        id=Event.make_id(title, start_dt),
                        title=title,
                        description=item.get("description", ""),
                        start_dt=start_dt,
                        venue_name=item.get("venue_name") or "",
                        address=item.get("address") or "",
                        city="Toronto",
                        url=item.get("url") or source_url,
                        source=source,
                        price=self._safe_price(item.get("price")),
                        raw_text=None,  # already extracted
                    )
                )
            except (ValueError, TypeError, KeyError):
                logger.debug("Skipping malformed extracted event: %s", item)
                continue
        return events

    @staticmethod
    def _safe_price(value: Any) -> str:
        if not value:
            return "Unknown"
        s = str(value).strip()
        return s if s else "Unknown"
