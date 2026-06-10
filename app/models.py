"""
SQLAlchemy ORM models.

    MENU_ITEMS  1───<  ORDER_ITEMS  >───1  ORDERS  >───1  TABLES
                                             │
                                             └──<  SERVICE_REQUESTS

An Order has many OrderItems. Each OrderItem points to one MenuItem and
remembers the price at the moment of ordering (`price_at_time`) so changing a
menu price later never rewrites history.

TABLES exist so a QR code can deep-link a guest straight to a table's menu.
SERVICE_REQUESTS are lightweight "call waiter" / "bring the bill" signals.

──────────────────────── Tenant isolation (RLS) ────────────────────────
Every row carries `restaurant_id`. The MVP enforces isolation at the
APPLICATION layer: every query filters on restaurant_id (see
app/tenancy.py). On production Postgres (Neon) this will be hardened with
true row-level-security POLICIES so the database itself blocks any
cross-restaurant read/write even if app code forgets a filter. SQLite (dev)
has no RLS, so app-level scoping is the source of truth for now.
"""

import uuid
from datetime import datetime, timezone

from datetime import date as date_type

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    """Generate a string UUID primary key (portable across SQLite/Postgres)."""
    return str(uuid.uuid4())


def _now() -> datetime:
    """Timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    # appetizers | mains | desserts | drinks
    category: Mapped[str] = mapped_column(String, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # Free-form labels for dietary filtering, e.g. ["vegan", "gluten-free"].
    # Stored as JSON so it works on both SQLite and Postgres.
    dietary_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # "86" toggle: when false the item is sold out / hidden from ordering,
    # without deleting it. Non-destructive.
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Table(Base):
    """A physical table. Its `id` (an unguessable UUID) is what the QR code
    encodes — NOT the human-friendly `number`. That way a leaked/old table
    number can't be used to impersonate a table."""
    __tablename__ = "tables"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    # free | occupied — set manually by staff (e.g. a walk-in that didn't
    # order through the app). Not auto-linked to orders.
    status: Mapped[str] = mapped_column(String, default="free", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    # Human-friendly per-restaurant, per-DAY counter (1, 2, 3...). Resets each
    # calendar day. The UUID `id` stays the real key; this is just for display.
    daily_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Lifecycle:
    #   pending   -> just placed, waiter must confirm with the guests
    #   confirmed -> waiter ok'd it, kitchen can cook
    #   ready     -> kitchen finished, waiter must deliver
    #   served    -> delivered (terminal)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)

    # dine_in | takeaway. Table orders are always dine_in.
    order_type: Mapped[str] = mapped_column(String, default="dine_in")
    # Null for web/takeaway orders not tied to a physical table.
    table_id: Mapped[str | None] = mapped_column(
        ForeignKey("tables.id"), nullable=True, index=True
    )

    total_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    bill_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )
    table: Mapped["Table | None"] = relationship()

    @property
    def table_number(self) -> int | None:
        """Convenience for kitchen/admin views: 'Table 4' without a join.
        Read-only; serialized by OrderOut."""
        return self.table.number if self.table else None


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id"), index=True, nullable=False
    )
    menu_item_id: Mapped[str] = mapped_column(
        ForeignKey("menu_items.id"), nullable=False
    )
    # Snapshots taken at order time so kitchen/bar tickets and history stay
    # readable even if the menu item is later renamed or deleted.
    menu_item_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    menu_item_category: Mapped[str] = mapped_column(String, nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_at_time: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    special_requests: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")
    menu_item: Mapped["MenuItem"] = relationship()


class Reservation(Base):
    """An online table booking.

    Lifecycle:
        pending   -> guest requested it, staff must approve
        confirmed -> staff approved (email confirmation will fire here later)
        declined  -> staff rejected
        cancelled -> guest cancelled (via the unguessable reservation UUID)

    We deliberately don't enforce real capacity/seating rules — staff approve
    or decline each request, so availability is advisory only.
    """
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    # Collected so we can email a confirmation later (not wired up yet).
    customer_email: Mapped[str] = mapped_column(String, nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String, nullable=True)

    party_size: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date_type] = mapped_column(Date, index=True, nullable=False)
    # Time slot as "HH:MM" (keeps it simple + timezone-free for the MVP).
    time: Mapped[str] = mapped_column(String, nullable=False)

    table_id: Mapped[str | None] = mapped_column(
        ForeignKey("tables.id"), nullable=True, index=True
    )

    # pending | confirmed | declined | cancelled
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    special_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    table: Mapped["Table | None"] = relationship()


class Voucher(Base):
    """A discount voucher.

    - `code`: short (<=5 char) human-typeable code the guest presents.
    - `percentage`: the discount, 1-100.
    - Lifecycle: active -> redeemed (staff/admin validate it on the voucher
      subscreen). One-time use.
    - `customer_email`: who it was emailed to (optional).
    """
    __tablename__ = "vouchers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(5), index=True, nullable=False)
    percentage: Mapped[int] = mapped_column(Integer, nullable=False)
    # active | redeemed
    status: Mapped[str] = mapped_column(String, default="active", index=True)
    customer_email: Mapped[str | None] = mapped_column(String, nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("reservations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ServiceRequest(Base):
    """A guest asking for something from the floor staff:
       - call_waiter : "come to my table"
       - bill        : "we're done, bring the check"
    Staff resolve these from a dashboard."""
    __tablename__ = "service_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    # call_waiter | bill
    type: Mapped[str] = mapped_column(String, nullable=False)
    # open | resolved
    status: Mapped[str] = mapped_column(String, default="open", index=True)

    table_id: Mapped[str | None] = mapped_column(
        ForeignKey("tables.id"), nullable=True
    )
    # Denormalised so staff can read "Table 4" without a join.
    table_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
