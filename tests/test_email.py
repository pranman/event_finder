"""
tests/test_email.py — Unit tests for email renderer and sender.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import smtplib

import pytest

from email_digest.renderer import EmailRenderer
from email_digest.sender import GmailSender
from models import Category, DigestSummary, LocationZone
from tests.conftest import make_event


# ──────────────────────────────────────────────
# Renderer tests
# ──────────────────────────────────────────────

class TestEmailRenderer:

    @pytest.fixture
    def digest(self, all_sample_events, now):
        top_picks = [e for e in all_sample_events if Category.TECH in e.categories]
        return DigestSummary(
            generated_at=now,
            date_range_start=now,
            date_range_end=now + timedelta(days=10),
            total_events=len(all_sample_events),
            events_by_category={"tech": 1, "concerts": 1, "running": 1},
            events_by_zone={"Fort York": 2, "Downtown Core": 1},
            top_picks=top_picks,
            all_events=all_sample_events,
        )

    def test_render_returns_html_string(self, digest):
        renderer = EmailRenderer()
        html = renderer.render(digest, user_email="test@example.com")
        assert isinstance(html, str)
        assert len(html) > 100

    def test_render_includes_event_title(self, digest, sample_tech_event):
        renderer = EmailRenderer()
        html = renderer.render(digest, user_email="test@example.com")
        assert sample_tech_event.title in html

    def test_render_includes_total_count(self, digest):
        renderer = EmailRenderer()
        html = renderer.render(digest, user_email="test@example.com")
        assert str(digest.total_events) in html

    def test_render_includes_user_email(self, digest):
        renderer = EmailRenderer()
        html = renderer.render(digest, user_email="moaddeli.m@gmail.com")
        assert "moaddeli.m@gmail.com" in html

    def test_render_is_valid_html(self, digest):
        renderer = EmailRenderer()
        html = renderer.render(digest, user_email="test@example.com")
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_render_with_no_events(self, now):
        renderer = EmailRenderer()
        empty_digest = DigestSummary(
            generated_at=now,
            date_range_start=now,
            date_range_end=now + timedelta(days=10),
            total_events=0,
            events_by_category={},
            events_by_zone={},
            top_picks=[],
            all_events=[],
        )
        # Should not raise even with empty data
        html = renderer.render(empty_digest, user_email="test@example.com")
        assert isinstance(html, str)


# ──────────────────────────────────────────────
# Sender tests
# ──────────────────────────────────────────────

class TestGmailSender:

    def test_raises_without_credentials(self, monkeypatch):
        monkeypatch.delenv("GMAIL_SENDER", raising=False)
        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="credentials not configured"):
            GmailSender()

    def test_send_success(self, monkeypatch):
        monkeypatch.setenv("GMAIL_SENDER", "test@gmail.com")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-app-password")

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_smtp = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_smtp

            sender = GmailSender()
            result = sender.send(
                to="recipient@example.com",
                subject="Test Subject",
                html_body="<html><body>Test</body></html>",
            )

        assert result is True
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("test@gmail.com", "test-app-password")
        mock_smtp.sendmail.assert_called_once()

    def test_send_returns_false_on_auth_error(self, monkeypatch):
        monkeypatch.setenv("GMAIL_SENDER", "test@gmail.com")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "bad-password")

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_smtp = MagicMock()
            mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Bad credentials")
            mock_smtp_class.return_value.__enter__.return_value = mock_smtp

            sender = GmailSender()
            result = sender.send("to@example.com", "Subject", "<html>Test</html>")

        assert result is False

    def test_build_subject(self):
        subject = GmailSender.build_subject(42, "Jun 14–Jun 21")
        assert "42" in subject
        assert "Jun 14–Jun 21" in subject
        assert "🗓️" in subject

    def test_strip_html(self):
        html = "<h1>Title</h1><p>Body text <a href='x'>link</a></p>"
        plain = GmailSender._strip_html(html)
        assert "Title" in plain
        assert "Body text" in plain
        assert "<" not in plain
