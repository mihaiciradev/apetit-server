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
import socket
import ssl
import time
from email.message import EmailMessage

from app import config

logger = logging.getLogger("apetit.email")


def _diagnose_connectivity(host: str, port: int) -> None:
    """
    Log DNS resolution and a raw TCP connect attempt to every resolved
    address (IPv4 + IPv6 separately), with per-address timing and errno.

    Purely diagnostic — never raises. The goal is that the Render logs show
    exactly which addresses were tried and why each one failed, instead of
    the misleading single `Errno 101` that create_connection surfaces.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception as e:  # noqa: BLE001
        logger.error("DNS resolution FAILED for %s:%s — %r", host, port, e)
        return

    fam_name = {socket.AF_INET: "IPv4", socket.AF_INET6: "IPv6"}
    addrs = [(fam_name.get(f, str(f)), sa[0]) for f, _, _, _, sa in infos]
    logger.info("DNS %s:%s resolved to %s", host, port, addrs)

    for family, _stype, _proto, _canon, sockaddr in infos:
        label = f"{fam_name.get(family, family)} {sockaddr[0]}"
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(10)
        start = time.monotonic()
        try:
            s.connect(sockaddr)
            dt = time.monotonic() - start
            logger.info("TCP connect OK to %s in %.2fs", label, dt)
        except OSError as e:
            dt = time.monotonic() - start
            logger.warning(
                "TCP connect FAILED to %s after %.2fs — errno=%s %s",
                label, dt, e.errno, e.strerror or e,
            )
        finally:
            s.close()


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

    context = ssl.create_default_context()
    logger.info(
        "sending email to %s via %s:%s (ssl=%s, user=%s)",
        to, config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USE_SSL,
        config.SMTP_USERNAME,
    )
    _diagnose_connectivity(config.SMTP_HOST, config.SMTP_PORT)
    try:
        if config.SMTP_USE_SSL:
            # Implicit SSL (port 465): TLS handshake happens on connect.
            server = smtplib.SMTP_SSL(
                config.SMTP_HOST, config.SMTP_PORT, timeout=20, context=context
            )
        else:
            # STARTTLS (port 587): connect plaintext, then upgrade.
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
        with server:
            if not config.SMTP_USE_SSL:
                server.starttls(context=context)
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("sent email to %s — %r", to, subject)
        return True
    except Exception:  # noqa: BLE001 — we deliberately swallow + log
        logger.exception(
            "failed to send email to %s (host=%s port=%s ssl=%s)",
            to, config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USE_SSL,
        )
        return False
