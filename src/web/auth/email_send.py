"""Send password reset emails via SMTP."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from src.settings import (
    APP_BASE_URL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USER,
    smtp_configured,
)

logger = logging.getLogger(__name__)


def send_password_reset_email(to_email: str, reset_url: str) -> None:
    if not smtp_configured():
        raise RuntimeError("SMTP is not configured")

    msg = EmailMessage()
    msg["Subject"] = "Reset your Job Crawler password"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(
        f"Someone requested a password reset for your Job Crawler account.\n\n"
        f"Reset your password:\n{reset_url}\n\n"
        f"This link expires in 1 hour. If you did not request this, ignore this email.\n"
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    logger.info("Password reset email sent to %s", to_email)


def build_reset_url(raw_token: str) -> str:
    return f"{APP_BASE_URL}/reset-password?token={raw_token}"
