"""
Central configuration for the APETIT backend.

For the MVP there is no authentication and no Restaurants table yet, so we
hardcode a single restaurant id. Every row we read/write is scoped to this id,
which means the day we add real multi-tenancy + auth, we only have to change
*where this value comes from* (the JWT / subdomain) and not the query logic.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Where the database lives. Defaults to a local SQLite file for dev.
# In production set DATABASE_URL to your Postgres connection string.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

# Fly Managed Postgres and Neon often hand out a "postgres://" URL, but
# SQLAlchemy 2.0 requires the "postgresql://" scheme. Normalise it so either
# works without thinking about it.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# MVP: single tenant, hardcoded. Later this comes from auth / subdomain.
DEFAULT_RESTAURANT_ID = "restaurant-a"

# Bookable time slots offered to guests. Availability is advisory — staff
# approve/decline each reservation, so this doesn't model real capacity.
RESERVATION_SLOTS = [
    "12:00", "13:00", "14:00",
    "18:00", "19:00", "20:00", "21:00",
]

# How long (minutes) a table stays "occupied" before the waiter is PROMPTED to
# confirm whether it's free. We never auto-free silently — we just surface a
# review prompt after this window, based on WHY it became occupied.
TABLE_REVIEW_MINUTES = {
    "scan": 10,     # someone scanned the QR but may have just been browsing
    "order": 45,    # they actually ordered — give them a full sitting
    "manual": 90,   # staff set it; longest leash before nudging
}

# ─────────────────────────── Email (SMTP) ───────────────────────────
# Master switch. While false, all email is a logged no-op (safe for dev).
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
# What guests see as the sender. Defaults to the SMTP user if unset.
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USERNAME)
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "APETIT")

# Public base URL of the frontend, used to build links inside emails
# (e.g. the "cancel my booking" link).
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
