"""
scheduler/setup_cron.py — Manages two Mac cron jobs for events_finder:

  1. Daily fast poll (default 9 AM) — cheap, no LLM, instant alerts.
  2. Weekly digest (Thursday 8 AM)  — all sources, LLM extraction, email.

Usage
-----
  python scheduler/setup_cron.py --install
  python scheduler/setup_cron.py --remove
  python scheduler/setup_cron.py --status
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_DIGEST_MARKER = "# events_finder digest"
_FAST_POLL_MARKER = "# events_finder fast-poll"

_DIGEST_SCHEDULE = "0 8 * * 4"     # Thursday 8 AM
_FAST_POLL_SCHEDULE = "0 9 * * *"  # Daily 9 AM


def _python() -> str:
    return sys.executable


def _server() -> str:
    return str(Path(__file__).parent.parent / "server.py")


def _log(name: str) -> str:
    return str(Path(__file__).parent.parent / f"{name}.log")


def _build_digest_line(schedule: str = _DIGEST_SCHEDULE) -> str:
    return (
        f"{schedule} {_python()} {_server()} --send"
        f" >> {_log('digest')} 2>&1 {_DIGEST_MARKER}"
    )


def _build_fast_poll_line(schedule: str = _FAST_POLL_SCHEDULE) -> str:
    return (
        f"{schedule} {_python()} {_server()} --fast-poll"
        f" >> {_log('fast_poll')} 2>&1 {_FAST_POLL_MARKER}"
    )


def _get_crontab() -> str:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return result.stdout if result.returncode == 0 else ""
    except FileNotFoundError:
        return ""


def _set_crontab(content: str) -> None:
    proc = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to set crontab: {proc.stderr}")


def install(
    digest_schedule: str = _DIGEST_SCHEDULE,
    fast_poll_schedule: str = _FAST_POLL_SCHEDULE,
) -> None:
    current = _get_crontab()
    lines = current.rstrip("\n").splitlines()
    added: list[str] = []

    if _DIGEST_MARKER not in current:
        lines.append(_build_digest_line(digest_schedule))
        added.append(f"Weekly digest: {digest_schedule}")
    else:
        print("  Weekly digest cron already installed.")

    if _FAST_POLL_MARKER not in current:
        lines.append(_build_fast_poll_line(fast_poll_schedule))
        added.append(f"Fast poll: {fast_poll_schedule}")
    else:
        print("  Fast-poll cron already installed.")

    if added:
        _set_crontab("\n".join(lines) + "\n")
        for desc in added:
            print(f"Installed: {desc}")
    else:
        print("Both cron jobs already installed. Use --remove first to update.")


def remove() -> None:
    current = _get_crontab()
    new_lines = [
        l for l in current.splitlines()
        if _DIGEST_MARKER not in l and _FAST_POLL_MARKER not in l
    ]
    _set_crontab("\n".join(new_lines) + "\n")
    print("events_finder cron jobs removed.")


def status() -> None:
    current = _get_crontab()
    found = False
    for line in current.splitlines():
        if _DIGEST_MARKER in line or _FAST_POLL_MARKER in line:
            print(f"Active: {line}")
            found = True
    if not found:
        print("No events_finder cron jobs installed.")
        print("Run: python scheduler/setup_cron.py --install")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage events_finder cron jobs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--remove", action="store_true")
    group.add_argument("--status", action="store_true")
    parser.add_argument("--digest-schedule", default=_DIGEST_SCHEDULE)
    parser.add_argument("--fast-poll-schedule", default=_FAST_POLL_SCHEDULE)
    args = parser.parse_args()

    if args.install:
        install(args.digest_schedule, args.fast_poll_schedule)
    elif args.remove:
        remove()
    elif args.status:
        status()
