"""
Email templates + orchestration.

Each function builds a subject + body and hands off to emailer.send_email.
They take plain values (not ORM objects), because they run in FastAPI
BackgroundTasks AFTER the request's DB session has closed — passing detached
ORM instances would blow up on lazy attribute access.

Wire-up: routers schedule these via `background_tasks.add_task(...)`.
"""

from datetime import date as date_type

from app import config
from app.emailer import send_email


def _cancel_link(reservation_id: str) -> str:
    return f"{config.FRONTEND_URL}/reservations/{reservation_id}"


def send_reservation_received(
    to_email: str,
    customer_name: str,
    date: date_type,
    time: str,
    party_size: int,
    reservation_id: str,
) -> None:
    """Guest just submitted a booking request (status=pending)."""
    link = _cancel_link(reservation_id)
    subject = "We received your reservation request"
    text = (
        f"Hi {customer_name},\n\n"
        f"Thanks for your reservation request:\n"
        f"  Date: {date}\n"
        f"  Time: {time}\n"
        f"  Party size: {party_size}\n\n"
        f"It's now pending approval — we'll email you again once it's confirmed.\n\n"
        f"Need to cancel? Use this link:\n{link}\n\n"
        f"— {config.MAIL_FROM_NAME}"
    )
    html = f"""\
    <p>Hi {customer_name},</p>
    <p>Thanks for your reservation request:</p>
    <ul>
      <li><strong>Date:</strong> {date}</li>
      <li><strong>Time:</strong> {time}</li>
      <li><strong>Party size:</strong> {party_size}</li>
    </ul>
    <p>It's now <strong>pending approval</strong> — we'll email you again once it's confirmed.</p>
    <p>Need to cancel? <a href="{link}">Manage your booking here</a>.</p>
    <p>— {config.MAIL_FROM_NAME}</p>
    """
    send_email(to_email, subject, text, html)


def send_reservation_confirmed(
    to_email: str,
    customer_name: str,
    date: date_type,
    time: str,
    party_size: int,
    reservation_id: str,
) -> None:
    """Staff approved the booking (status=confirmed)."""
    link = _cancel_link(reservation_id)
    subject = "Your reservation is confirmed ✅"
    text = (
        f"Hi {customer_name},\n\n"
        f"Good news — your reservation is confirmed:\n"
        f"  Date: {date}\n"
        f"  Time: {time}\n"
        f"  Party size: {party_size}\n\n"
        f"Plans changed? Cancel here:\n{link}\n\n"
        f"See you soon!\n— {config.MAIL_FROM_NAME}"
    )
    html = f"""\
    <p>Hi {customer_name},</p>
    <p>Good news — your reservation is <strong>confirmed</strong>:</p>
    <ul>
      <li><strong>Date:</strong> {date}</li>
      <li><strong>Time:</strong> {time}</li>
      <li><strong>Party size:</strong> {party_size}</li>
    </ul>
    <p>Plans changed? <a href="{link}">Cancel here</a>.</p>
    <p>See you soon!<br>— {config.MAIL_FROM_NAME}</p>
    """
    send_email(to_email, subject, text, html)


def send_voucher_email(to_email: str, percentage: int, code: str) -> None:
    """Admin granted a discount voucher to a guest."""
    subject = f"Here's a {percentage}% discount voucher 🎁"
    text = (
        f"Hello,\n\n"
        f"As a thank-you, here's a {percentage}% discount voucher:\n\n"
        f"  CODE: {code}\n\n"
        f"Show this code to our staff on your next visit to redeem it.\n\n"
        f"— {config.MAIL_FROM_NAME}"
    )
    html = f"""\
    <p>Hello,</p>
    <p>As a thank-you, here's a <strong>{percentage}% discount voucher</strong>:</p>
    <p style="font-size:24px;font-weight:bold;letter-spacing:3px;">{code}</p>
    <p>Show this code to our staff on your next visit to redeem it.</p>
    <p>— {config.MAIL_FROM_NAME}</p>
    """
    send_email(to_email, subject, text, html)
