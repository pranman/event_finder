"""
scrapers/base.py — Abstract base class for all event scrapers.

Every scraper must:
  - Accept a shared httpx.AsyncClient (so tests can inject a mock client)
  - Implement `fetch(date_from, date_to) -> list[Event]`
  - Return only canonical Event objects (no raw API data leaks out)
  - Handle its own errors gracefully (log + return empty list, never raise)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime

import httpx

from models import Event

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """
    Abstract scraper interface.

    Parameters
    ----------
    client:
        A shared ``httpx.AsyncClient``. Injected so tests can swap in a
        mock via ``respx`` without monkey-patching global state.
    """

    #: Human-readable name shown in logs and the digest email source badge.
    SOURCE_NAME: str = "unknown"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._log = logging.getLogger(f"scrapers.{self.SOURCE_NAME}")

    # ── public API ────────────────────────────────────────────────────

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        """
        Fetch events in ``[date_from, date_to]`` and return a list of
        canonical :class:`~models.Event` objects.

        Exceptions are caught here so a failing scraper never crashes the
        whole pipeline — it just returns an empty list and logs the error.
        """
        try:
            return await self._fetch(date_from, date_to)
        except httpx.HTTPStatusError as exc:
            self._log.error(
                "HTTP %s from %s: %s",
                exc.response.status_code,
                self.SOURCE_NAME,
                exc.request.url,
            )
        except httpx.RequestError as exc:
            self._log.error("Network error from %s: %s", self.SOURCE_NAME, exc)
        except Exception:  # noqa: BLE001
            self._log.exception("Unexpected error in %s scraper", self.SOURCE_NAME)
        return []

    # ── subclass hook ─────────────────────────────────────────────────

    @abstractmethod
    async def _fetch(self, date_from: datetime, date_to: datetime) -> list[Event]:
        """Internal implementation — override in each concrete scraper."""
        ...

    # ── shared helpers ────────────────────────────────────────────────

    @staticmethod
    def _safe_price(value: str | float | int | None) -> str:
        """Normalise a raw price field to a human-readable string."""
        if value is None or value == "":
            return "Unknown"
        if isinstance(value, (int, float)):
            return "Free" if value == 0 else f"${value:.0f}"
        val = str(value).strip()
        if val.lower() in {"0", "0.0", "free", ""}:
            return "Free"
        return val
