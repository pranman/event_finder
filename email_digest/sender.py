"""
email_digest/sender.py — Gmail SMTP email sender.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class GmailSender:
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

    def send(self, to: str, subject: str, html_body: str, plain_body: str | None = None) -> bool:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Toronto Events Finder <{self._sender}>"
        msg["To"] = to
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
            logger.error("Gmail auth failed — use an App Password, not your real password.")
        except smtplib.SMTPException as exc:
            logger.error("SMTP error: %s", exc)
        except OSError as exc:
            logger.error("Network error: %s", exc)
        return False

    @staticmethod
    def _strip_html(html: str) -> str:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s{2,}", " ", text).strip()

    @staticmethod
    def build_subject(total_events: int, date_range: str) -> str:
        return f"🗓️ {total_events} Toronto Events This Weekend · {date_range}"

    @staticmethod
    def build_alert_subject(event_title: str) -> str:
        return f"⚡ Act fast: {event_title}"
