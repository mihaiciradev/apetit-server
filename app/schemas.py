"""
Pydantic schemas = the shape of data crossing the API boundary.

Two jobs:
1. Validate incoming JSON (request bodies) before it touches the DB.
2. Serialize ORM objects into clean JSON responses.

Naming convention:
- `XCreate`  -> what the client sends to create something
- `XOut`     -> what we send back
"""

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── Menu ───────────────────────────

# Valid menu categories. Keeping this as a regex on the schema means a typo
# like "main" or "drink" gets rejected with a 422 before it hits the DB.
_CATEGORY_PATTERN = "^(appetizers|mains|desserts|drinks)$"


class MenuItemCreate(BaseModel):
    """Body for creating a menu item (admin)."""
    name: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    category: str = Field(pattern=_CATEGORY_PATTERN)
    description: str | None = None
    image_url: str | None = None
    dietary_tags: list[str] = Field(default_factory=list)
    available: bool = True


class MenuItemUpdate(BaseModel):
    """Body for editing a menu item (admin). Every field optional — send only
    what changes (PATCH-style semantics). Set available=false to '86' an item."""
    name: str | None = Field(default=None, min_length=1)
    price: Decimal | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, pattern=_CATEGORY_PATTERN)
    description: str | None = None
    image_url: str | None = None
    dietary_tags: list[str] | None = None
    available: bool | None = None


class MenuItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # allow ORM -> schema

    id: str
    name: str
    price: Decimal
    category: str
    description: str | None = None
    image_url: str | None = None
    dietary_tags: list[str] = []
    available: bool = True


# ─────────────────────────── Tables ───────────────────────────

class TableOut(BaseModel):
    """Returned when a guest's QR resolves a table. We expose the friendly
    `number` plus the `id` the client already has."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: int
    status: str = "free"   # free | occupied
    occupied_at: datetime | None = None


class TableCreate(BaseModel):
    """Admin creates a table by its human number; the UUID is generated."""
    number: int = Field(gt=0)


class TableStatusUpdate(BaseModel):
    """Staff mark a table busy/free manually (walk-ins, etc.)."""
    status: str = Field(pattern="^(free|occupied)$")


# ─────────────────────────── Orders ───────────────────────────

class OrderItemCreate(BaseModel):
    """One line in a new order. Price is looked up server-side, never trusted
    from the client."""
    menu_item_id: str
    quantity: int = Field(gt=0, description="Must order at least 1")
    special_requests: str | None = None


class OrderCreate(BaseModel):
    """Body for POST /api/orders. We compute the total ourselves.

    Two ways to order:
      - From a table QR: send `table_id`. order_type is forced to dine_in.
      - From the web   : omit table_id, but you MUST send order_type
                         (dine_in or takeaway). Payment happens in person.
    """
    items: list[OrderItemCreate] = Field(min_length=1)
    table_id: str | None = None
    order_type: str | None = Field(
        default=None, pattern="^(dine_in|takeaway)$"
    )


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    menu_item_id: str
    menu_item_name: str          # snapshot — safe even if the item is deleted
    menu_item_category: str      # lets the bar filter drinks, KDS group by station
    quantity: int
    price_at_time: Decimal
    special_requests: str | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    daily_number: int
    status: str
    order_type: str
    table_id: str | None = None
    table_number: int | None = None   # denormalised for kitchen/admin display
    total_price: Decimal
    bill_requested: bool
    created_at: datetime
    completed_at: datetime | None = None
    items: list[OrderItemOut] = []


# ─────────────────────────── Kitchen / staff ───────────────────────────

class OrderStatusUpdate(BaseModel):
    """Body for PATCH .../status. Constrained to the valid lifecycle states.
    `cancelled` voids a mistaken/abandoned order (terminal, excluded from
    revenue)."""
    status: str = Field(pattern="^(pending|confirmed|ready|served|cancelled)$")


# ─────────────────────────── Reservations ───────────────────────────

# Loose email check — good enough without pulling in email-validator.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ReservationCreate(BaseModel):
    """Body for POST /api/reservations (guest-facing).

    Guests pick date + time + party size only — NOT a table. Staff assign a
    table (if they want) when approving. customer_phone / special_notes may be
    omitted entirely (no need to send explicit null)."""
    customer_name: str = Field(min_length=1)
    customer_email: str = Field(pattern=_EMAIL_PATTERN)
    customer_phone: str | None = None
    party_size: int = Field(gt=0)
    date: date_type
    time: str = Field(description="One of the configured slots, e.g. '19:00'")
    special_notes: str | None = None


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_name: str
    customer_email: str
    customer_phone: str | None = None
    party_size: int
    date: date_type
    time: str
    table_id: str | None = None
    status: str            # pending | confirmed | declined | cancelled
    special_notes: str | None = None
    created_at: datetime


class ReservationStatusUpdate(BaseModel):
    """Staff approve/decline. Optionally assign a table in the same call
    (handy on approval). (Guests cancel via a dedicated endpoint.)"""
    status: str = Field(pattern="^(confirmed|declined)$")
    table_id: str | None = None


class ReservationTableAssign(BaseModel):
    """Assign / reassign a table to a reservation independently of status."""
    table_id: str | None = None   # null clears the assignment


class SlotAvailability(BaseModel):
    time: str
    available: bool   # MVP: always true (staff approve/decline each request)


class AvailabilityOut(BaseModel):
    date: date_type
    slots: list[SlotAvailability]


# ─────────────────────────── Vouchers ───────────────────────────

class VoucherCreate(BaseModel):
    """Admin creates a voucher; the FE only chooses the percentage (and,
    optionally, which customer to email it to)."""
    percentage: int = Field(gt=0, le=100)
    customer_email: str | None = Field(default=None, pattern=_EMAIL_PATTERN)
    reservation_id: str | None = None


class VoucherRedeem(BaseModel):
    """Staff/admin validate a code from the voucher subscreen."""
    code: str = Field(min_length=1, max_length=5)


class VoucherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    percentage: int
    status: str            # active | redeemed
    customer_email: str | None = None
    reservation_id: str | None = None
    created_at: datetime
    redeemed_at: datetime | None = None


# ─────────────────────────── Service requests ───────────────────────────

class ServiceRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str            # call_waiter | bill
    status: str          # open | resolved
    table_id: str | None = None
    table_number: int | None = None
    order_id: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


# ─────────────────────────── Admin ───────────────────────────

class StatsOut(BaseModel):
    total_orders: int
    total_revenue: Decimal
    pending_orders: int
    # Peak-hours data: "HH" -> order count. Lets the admin chart busy times.
    orders_by_hour: dict[str, int] = {}


class TopItem(BaseModel):
    menu_item_name: str
    quantity: int
    revenue: Decimal


class AnalyticsOut(BaseModel):
    """Richer manager analytics over a date range."""
    total_orders: int
    total_revenue: Decimal
    orders_by_hour: dict[str, int] = {}
    revenue_by_day: dict[str, Decimal] = {}   # "YYYY-MM-DD" -> revenue
    top_items: list[TopItem] = []


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str          # screen that triggered it
    method: str          # POST | PUT | PATCH | DELETE
    path: str            # what was acted on
    status_code: int
    created_at: datetime


class StaffSummaryOut(BaseModel):
    """One-shot counts for staff/admin tab badges (replaces 4 list polls)."""
    pending_orders: int
    open_service_requests: int
    pending_reservations: int
    occupied_tables: int
