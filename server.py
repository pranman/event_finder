"""
server.py — events_finder MCP Server.

Exposes 5 tools to the MCP host (e.g. Claude Desktop, Gemini):

  fetch_events      — Pull fresh events and return a summary
  preview_digest    — Show what this week's email would contain (no send)
  send_digest_now   — Force-send the digest email immediately
  list_events       — Query events by category / zone / date
  update_config     — Change preferences live (categories, location, etc.)

Run with:
  python server.py          # start MCP server
  python server.py --test   # dry run, print digest to stdout
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from email_digest.renderer import EmailRenderer
from email_digest.sender import GmailSender
from models import Category, LocationZone
from pipeline import load_config, run_pipeline

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("server")

mcp = FastMCP("events_finder")


# ──────────────────────────────────────────────
# Tool: fetch_events
# ──────────────────────────────────────────────

@mcp.tool()
async def fetch_events(dry_run: bool = False) -> str:
    """
    Fetch upcoming Toronto events from all sources and return a summary.

    Parameters
    ----------
    dry_run : bool
        If True, uses sample fixture data instead of hitting real APIs.
        Useful for testing without API keys.

    Returns
    -------
    str
        JSON summary with event counts by category and zone.
    """
    config = load_config()
    digest = await run_pipeline(config, dry_run=dry_run)
    return json.dumps({
        "total_events": digest.total_events,
        "date_range": f"{digest.date_range_start.date()} to {digest.date_range_end.date()}",
        "by_category": digest.events_by_category,
        "by_zone": digest.events_by_zone,
        "top_picks": [
            {
                "title": e.title,
                "date": e.day_label,
                "time": e.time_label,
                "venue": e.venue_name,
                "zone": e.location_zone.value,
                "url": e.url,
                "score": e.relevance_score,
            }
            for e in digest.top_picks
        ],
    }, indent=2)


# ──────────────────────────────────────────────
# Tool: preview_digest
# ──────────────────────────────────────────────

@mcp.tool()
async def preview_digest(dry_run: bool = False) -> str:
    """
    Preview the digest email content without sending it.

    Returns a structured text preview of all categorized events.
    Useful to check what would be in this week's email.
    """
    config = load_config()
    digest = await run_pipeline(config, dry_run=dry_run)

    lines = [
        f"📅 Digest Preview — {digest.date_range_start.strftime('%b %-d')} to {digest.date_range_end.strftime('%b %-d')}",
        f"Total: {digest.total_events} events\n",
        "⭐ TOP PICKS",
    ]
    for e in digest.top_picks:
        lines.append(f"  • [{e.relevance_score:.2f}] {e.title} — {e.day_label} {e.time_label}")
        if e.venue_name:
            lines.append(f"    📍 {e.venue_name} ({e.location_zone.value})")
        lines.append(f"    🔗 {e.url}")

    from collections import defaultdict
    by_cat: dict[str, list] = defaultdict(list)
    for event in digest.all_events:
        for cat in event.categories:
            by_cat[cat.value].append(event)

    for cat_key, cat_events in by_cat.items():
        if not cat_events:
            continue
        lines.append(f"\n── {cat_key.upper()} ({len(cat_events)}) ──")
        for e in cat_events[:8]:
            lines.append(f"  {e.day_label} {e.time_label}  {e.title}")
            if e.venue_name:
                lines.append(f"    @ {e.venue_name}")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Tool: send_digest_now
# ──────────────────────────────────────────────

@mcp.tool()
async def send_digest_now(dry_run: bool = False) -> str:
    """
    Run the full pipeline and send the digest email immediately.

    Parameters
    ----------
    dry_run : bool
        If True, uses sample events and prints the email HTML instead
        of sending it. Safe to use for testing.

    Returns
    -------
    str
        Confirmation message with event count and send status.
    """
    config = load_config()
    digest = await run_pipeline(config, dry_run=dry_run)

    renderer = EmailRenderer()
    recipient = config["user"]["email"]
    html = renderer.render(digest, user_email=recipient)

    date_range = (
        f"{digest.date_range_start.strftime('%b %-d')}–"
        f"{digest.date_range_end.strftime('%b %-d')}"
    )
    subject = GmailSender.build_subject(digest.total_events, date_range)

    if dry_run:
        # Save HTML to a temp file so you can open it in a browser
        import tempfile, os
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, prefix="digest_preview_"
        ) as f:
            f.write(html)
            path = f.name
        return f"DRY RUN: digest rendered with {digest.total_events} events. Preview at: {path}"

    try:
        sender = GmailSender()
        success = sender.send(to=recipient, subject=subject, html_body=html)
        if success:
            return f"✅ Email sent to {recipient} with {digest.total_events} events."
        return f"❌ Failed to send email. Check logs for SMTP error details."
    except ValueError as exc:
        return f"❌ Gmail not configured: {exc}"


# ──────────────────────────────────────────────
# Tool: list_events
# ──────────────────────────────────────────────

@mcp.tool()
async def list_events(
    category: str | None = None,
    zone: str | None = None,
    date: str | None = None,
    dry_run: bool = False,
) -> str:
    """
    Query events by category, location zone, or date.

    Parameters
    ----------
    category : str, optional
        Filter by category: "tech", "concerts", "running", or "other".
    zone : str, optional
        Filter by zone: "Fort York", "Waterfront West", "Queen/King West",
        "Downtown Core", or "Midtown".
    date : str, optional
        Filter by date in YYYY-MM-DD format.
    dry_run : bool
        Use sample events instead of real API calls.

    Returns
    -------
    str
        JSON list of matching events.
    """
    config = load_config()
    digest = await run_pipeline(config, dry_run=dry_run)
    events = digest.all_events

    if category:
        try:
            cat_enum = Category(category.lower())
            events = [e for e in events if cat_enum in e.categories]
        except ValueError:
            return f"Unknown category '{category}'. Use: tech, concerts, running, other"

    if zone:
        events = [e for e in events if zone.lower() in e.location_zone.value.lower()]

    if date:
        try:
            filter_date = datetime.fromisoformat(date).date()
            events = [e for e in events if e.start_dt.date() == filter_date]
        except ValueError:
            return f"Invalid date format '{date}'. Use YYYY-MM-DD."

    return json.dumps([
        {
            "title": e.title,
            "date": e.day_label,
            "time": e.time_label,
            "venue": e.venue_name,
            "address": e.address,
            "zone": e.location_zone.value,
            "categories": [c.value for c in e.categories],
            "price": e.price,
            "url": e.url,
            "source": e.source.value,
            "score": e.relevance_score,
        }
        for e in events
    ], indent=2)


# ──────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    if "--test" in sys.argv or "--dry-run" in sys.argv:
        async def _test() -> None:
            result = await preview_digest(dry_run=True)
            print(result)
        asyncio.run(_test())

    elif "--send" in sys.argv:
        async def _send() -> None:
            result = await send_digest_now(dry_run=False)
            print(result)
        asyncio.run(_send())

    elif "--fast-poll" in sys.argv:
        from pipeline import run_fast_poll
        from email_digest.renderer import EmailRenderer
        from email_digest.sender import GmailSender

        async def _fast_poll() -> None:
            config = load_config()
            alert_events = await run_fast_poll(config, dry_run=False)
            if not alert_events:
                logger.info("Fast poll: no alerts to send.")
                return
            renderer = EmailRenderer()
            recipient = config["user"]["email"]
            sender = GmailSender()
            for event in alert_events:
                subject = GmailSender.build_alert_subject(event.title)
                html = renderer.render_alert(event, user_email=recipient)
                sender.send(to=recipient, subject=subject, html_body=html)
                logger.info("Alert sent for: %s", event.title)

        asyncio.run(_fast_poll())

    else:
        mcp.run()
