"""
Low-level email transport (SMTP).

One job: take a built message and send it. Templates/orchestration live in
notifications.py; this module only knows how to talk to the SMTP server.

Safety:
- Gated by config.EMAIL_ENABLED — while false, it logs and returns without
  connecting (so dev never sends real mail).
- Never raises: a send failure is logged and returns False. Callers run this
  from a BackgroundTask, so a mail problem must never break the request that
  triggered it (a booking is already committed before the email goes out).
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app import config

logger = logging.getLogger("apetit.email")


def send_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send one email. Returns True on success, False otherwise (never raises)."""
    if not config.EMAIL_ENABLED:
        logger.info("[email disabled] would send to %s — %r", to, subject)
        return False

    if not (config.SMTP_HOST and config.SMTP_USERNAME and config.SMTP_PASSWORD):
        logger.error("EMAIL_ENABLED but SMTP_* settings are incomplete; skipping.")
        return False

    msg = EmailMessage()
    msg["From"] = f"{config.MAIL_FROM_NAME} <{config.MAIL_FROM}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        print(config.SMTP_HOST, config.SMTP_PORT)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("sent email to %s — %r", to, subject)
        return True
    except Exception:  # noqa: BLE001 — we deliberately swallow + log
        logger.exception("failed to send email to %s", to)
        return False
