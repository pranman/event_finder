"""
email_digest/sender.py — Gmail SMTP email sender.

Uses Python's built-in smtplib with TLS/SSL and a Gmail App Password.
Never use your real Gmail password — only App Passwords work here.

Setup guide:
  1. Enable 2-Step Verification: myaccount.google.com → Security
  2. Create App Password: myaccount.google.com → Security → App Passwords
  3. Add to .env:  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

logger = logging.getLogger(__name__)


class GmailSender:
    """
    Sends HTML emails via Gmail SMTP (TLS on port 587).

    Parameters
    ----------
    sender_email:
        The Gmail address to send FROM (must match GMAIL_SENDER in .env).
    app_password:
        The 16-character Gmail App Password (not your regular password).
    """

    _SMTP_HOST = "smtp.gmail.com"
    _SMTP_PORT = 587

    def __init__(
        self,
        sender_email: str | None = None,
        app_password: str | None = None,
    ) -> None:
        self._sender = sender_email or os.getenv("GMAIL_SENDER", "")
        self._password = app_password or os.getenv("GMAIL_APP_PASSWORD", "")

        if not self._sender or not self._password:
            raise ValueError(
                "Gmail credentials not configured. "
                "Set GMAIL_SENDER and GMAIL_APP_PASSWORD in .env"
            )

    def send(
        self,
        to: str,
        subject: str,
        html_body: str,
        plain_body: str | None = None,
    ) -> bool:
        """
        Send an HTML email.

        Parameters
        ----------
        to:
            Recipient email address.
        subject:
            Email subject line.
        html_body:
            Full HTML content.
        plain_body:
            Optional plain-text fallback. Auto-generated if not provided.

        Returns
        -------
        bool
            True if sent successfully, False on any error.
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Toronto Events Finder <{self._sender}>"
        msg["To"] = to

        # Plain text fallback (strip tags naively)
        if plain_body is None:
            plain_body = self._strip_html(html_body)

        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(self._SMTP_HOST, self._SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(self._sender, self._password)
                server.sendmail(self._sender, to, msg.as_string())
            logger.info("Email sent successfully to %s", to)
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error(
                "Gmail authentication failed. "
                "Check GMAIL_APP_PASSWORD — use an App Password, not your real password."
            )
        except smtplib.SMTPException as exc:
            logger.error("SMTP error: %s", exc)
        except OSError as exc:
            logger.error("Network error sending email: %s", exc)
        return False

    @staticmethod
    def _strip_html(html: str) -> str:
        """Very basic HTML tag stripper for plain-text fallback."""
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    @staticmethod
    def build_subject(total_events: int, date_range: str) -> str:
        """Generate a compelling email subject line."""
        return f"🗓️ {total_events} Toronto Events This Weekend · {date_range}"
