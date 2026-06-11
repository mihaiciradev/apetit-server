"""
Admin routes (no auth yet in the MVP).

    Menu mgmt : POST/PUT/DELETE /api/admin/menu
    Tables    : GET/POST        /api/admin/tables   (to print QR codes)
    Orders    : GET /api/admin/orders
    Stats     : GET /api/admin/stats
"""

import secrets
from datetime import date as date_type
from datetime import datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import AuditLog, MenuItem, Order, Reservation, Table, Voucher
from app.notifications import send_voucher_email
from app.schemas import (
    AnalyticsOut,
    AuditLogOut,
    MenuItemCreate,
    MenuItemOut,
    MenuItemUpdate,
    OrderOut,
    ReservationOut,
    StatsOut,
    TableCreate,
    TableOut,
    TopItem,
    VoucherCreate,
    VoucherOut,
)
from app.tenancy import current_restaurant_id

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Code alphabet excludes easily-confused chars (0/O, 1/I) — codes are read
# aloud and typed by staff/guests.
_VOUCHER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _generate_voucher_code(db: Session, rid: str) -> str:
    """A unique 5-char code for this restaurant."""
    for _ in range(25):
        code = "".join(secrets.choice(_VOUCHER_ALPHABET) for _ in range(5))
        exists = db.scalar(
            select(Voucher).where(
                Voucher.restaurant_id == rid, Voucher.code == code
            )
        )
        if exists is None:
            return code
    raise HTTPException(status_code=500, detail="Could not generate a unique code")


def _created_at_bounds(
    start: date_type | None, end: date_type | None
) -> tuple[datetime | None, datetime | None]:
    """Turn inclusive start/end DATES into created_at datetime boundaries.
    `end` is inclusive (covers the whole day) via < end+1day."""
    lo = datetime.combine(start, time.min) if start else None
    hi = datetime.combine(end + timedelta(days=1), time.min) if end else None
    return lo, hi


# ─────────────────────────── Menu management ───────────────────────────
# The public GET /api/menu is read-only. These admin routes are how items
# actually get created / edited / removed.

@router.post("/menu", response_model=MenuItemOut, status_code=201)
def create_menu_item(
    payload: MenuItemCreate,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Add a new menu item."""
    item = MenuItem(
        restaurant_id=rid,
        name=payload.name,
        price=payload.price,
        category=payload.category,
        description=payload.description,
        image_url=payload.image_url,
        dietary_tags=payload.dietary_tags,
        available=payload.available,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/menu/{item_id}", response_model=MenuItemOut)
def update_menu_item(
    item_id: str,
    payload: MenuItemUpdate,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Edit an existing menu item. Only the fields you send get changed."""
    item = db.get(MenuItem, item_id)
    if item is None or item.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Menu item not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


@router.delete("/menu/{item_id}", status_code=204)
def delete_menu_item(
    item_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Remove a menu item. Past orders keep their price snapshot, so history
    stays intact."""
    item = db.get(MenuItem, item_id)
    if item is None or item.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Menu item not found")
    db.delete(item)
    db.commit()


# ─────────────────────────── Table management ───────────────────────────

@router.get("/tables", response_model=list[TableOut])
def list_tables(
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """All tables, so the admin can generate a QR per table id."""
    stmt = (
        select(Table)
        .where(Table.restaurant_id == rid)
        .order_by(Table.number)
    )
    return db.scalars(stmt).all()


@router.post("/tables", response_model=TableOut, status_code=201)
def create_table(
    payload: TableCreate,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Create a table; its UUID is what the QR code will encode."""
    table = Table(restaurant_id=rid, number=payload.number)
    db.add(table)
    db.commit()
    db.refresh(table)
    return table


# ─────────────────────────── Orders & stats ───────────────────────────

@router.get("/orders", response_model=list[OrderOut])
def all_orders(
    start: date_type | None = Query(None, description="Inclusive start date YYYY-MM-DD"),
    end: date_type | None = Query(None, description="Inclusive end date YYYY-MM-DD"),
    status: str | None = Query(None, description="Optional status filter"),
    limit: int | None = Query(None, ge=1, le=500, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Order history, most recent first. Optional date range (by created_at),
    status filter, and limit/offset paging. No params => everything."""
    lo, hi = _created_at_bounds(start, end)
    stmt = select(Order).where(Order.restaurant_id == rid)
    if lo is not None:
        stmt = stmt.where(Order.created_at >= lo)
    if hi is not None:
        stmt = stmt.where(Order.created_at < hi)
    if status:
        stmt = stmt.where(Order.status == status)
    stmt = stmt.order_by(Order.created_at.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


@router.get("/reservations", response_model=list[ReservationOut])
def all_reservations(
    start: date_type | None = Query(None, description="Inclusive start booking date"),
    end: date_type | None = Query(None, description="Inclusive end booking date"),
    status: str | None = Query(None, description="Optional status filter"),
    q: str | None = Query(None, description="Search customer name or email"),
    limit: int | None = Query(None, ge=1, le=500, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Reservation history, newest first. Optional date range (on BOOKING date),
    status filter, name/email search, and paging. No params => everything."""
    stmt = select(Reservation).where(Reservation.restaurant_id == rid)
    if start is not None:
        stmt = stmt.where(Reservation.date >= start)
    if end is not None:
        stmt = stmt.where(Reservation.date <= end)
    if status:
        stmt = stmt.where(Reservation.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            Reservation.customer_name.ilike(like)
            | Reservation.customer_email.ilike(like)
        )
    stmt = stmt.order_by(Reservation.created_at.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


# ─────────────────────────── Vouchers ───────────────────────────

@router.get("/vouchers", response_model=list[VoucherOut])
def list_vouchers(
    status: str | None = Query(None, description="active | redeemed"),
    q: str | None = Query(None, description="Search by code"),
    limit: int | None = Query(None, ge=1, le=500, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Vouchers, newest first. Optional status filter, code search, and paging."""
    stmt = select(Voucher).where(Voucher.restaurant_id == rid)
    if status:
        stmt = stmt.where(Voucher.status == status)
    if q:
        stmt = stmt.where(Voucher.code.ilike(f"%{q.strip().upper()}%"))
    stmt = stmt.order_by(Voucher.created_at.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


@router.post("/vouchers", response_model=VoucherOut, status_code=201)
def create_voucher(
    payload: VoucherCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Create a discount voucher. The FE just picks the percentage (and,
    optionally, a customer email to send it to). The code + email template are
    generated here."""
    voucher = Voucher(
        restaurant_id=rid,
        code=_generate_voucher_code(db, rid),
        percentage=payload.percentage,
        status="active",
        customer_email=payload.customer_email,
        reservation_id=payload.reservation_id,
    )
    db.add(voucher)
    db.commit()
    db.refresh(voucher)

    # Email it to the guest if an address was given.
    if voucher.customer_email:
        background_tasks.add_task(
            send_voucher_email,
            voucher.customer_email,
            voucher.percentage,
            voucher.code,
        )
    return voucher


@router.get("/logs", response_model=list[AuditLogOut])
def audit_logs(
    start: date_type | None = Query(None, description="Inclusive start date YYYY-MM-DD"),
    end: date_type | None = Query(None, description="Inclusive end date YYYY-MM-DD"),
    source: str | None = Query(None, description="Filter by screen, e.g. 'admin'"),
    method: str | None = Query(None, description="POST | PUT | PATCH | DELETE"),
    q: str | None = Query(None, description="Search in the path"),
    limit: int | None = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Audit trail of mutating actions, newest first. Filter by date range,
    source screen, method, or path search. Defaults to the latest 100."""
    lo, hi = _created_at_bounds(start, end)
    stmt = select(AuditLog).where(AuditLog.restaurant_id == rid)
    if lo is not None:
        stmt = stmt.where(AuditLog.created_at >= lo)
    if hi is not None:
        stmt = stmt.where(AuditLog.created_at < hi)
    if source:
        stmt = stmt.where(AuditLog.source == source)
    if method:
        stmt = stmt.where(AuditLog.method == method.upper())
    if q:
        stmt = stmt.where(AuditLog.path.ilike(f"%{q.strip()}%"))
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


@router.get("/analytics", response_model=AnalyticsOut)
def analytics(
    start: date_type | None = Query(None, description="Inclusive start date YYYY-MM-DD"),
    end: date_type | None = Query(None, description="Inclusive end date YYYY-MM-DD"),
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Manager analytics over a date range: revenue per day (line chart),
    busiest hours, and best-selling items. Excludes cancelled orders."""
    lo, hi = _created_at_bounds(start, end)
    stmt = (
        select(Order)
        .where(Order.restaurant_id == rid, Order.status != "cancelled")
        .options(selectinload(Order.items))
    )
    if lo is not None:
        stmt = stmt.where(Order.created_at >= lo)
    if hi is not None:
        stmt = stmt.where(Order.created_at < hi)
    orders = db.scalars(stmt).all()

    revenue_by_day: dict[str, Decimal] = {}
    orders_by_hour: dict[str, int] = {}
    total_revenue = Decimal("0")
    item_qty: dict[str, int] = {}
    item_rev: dict[str, Decimal] = {}

    for o in orders:
        rev = Decimal(str(o.total_price))
        total_revenue += rev
        if o.created_at is not None:
            day = o.created_at.date().isoformat()
            revenue_by_day[day] = revenue_by_day.get(day, Decimal("0")) + rev
            hour = f"{o.created_at.hour:02d}"
            orders_by_hour[hour] = orders_by_hour.get(hour, 0) + 1
        for it in o.items:
            line_rev = Decimal(str(it.price_at_time)) * it.quantity
            item_qty[it.menu_item_name] = item_qty.get(it.menu_item_name, 0) + it.quantity
            item_rev[it.menu_item_name] = item_rev.get(it.menu_item_name, Decimal("0")) + line_rev

    top_items = [
        TopItem(menu_item_name=name, quantity=qty, revenue=item_rev[name])
        for name, qty in sorted(item_qty.items(), key=lambda kv: kv[1], reverse=True)
    ][:10]

    return AnalyticsOut(
        total_orders=len(orders),
        total_revenue=total_revenue,
        orders_by_hour=orders_by_hour,
        revenue_by_day=revenue_by_day,
        top_items=top_items,
    )


@router.get("/stats", response_model=StatsOut)
def stats(
    start: date_type | None = Query(None, description="Inclusive start date YYYY-MM-DD"),
    end: date_type | None = Query(None, description="Inclusive end date YYYY-MM-DD"),
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Orders, revenue, pending count, and peak hours — over an optional date
    range (by created_at). No params => all time. Use this for day/week/month
    by passing the matching start/end."""
    lo, hi = _created_at_bounds(start, end)

    def _scoped(stmt):
        stmt = stmt.where(Order.restaurant_id == rid)
        if lo is not None:
            stmt = stmt.where(Order.created_at >= lo)
        if hi is not None:
            stmt = stmt.where(Order.created_at < hi)
        return stmt

    # Cancelled orders don't count toward sales numbers.
    not_cancelled = Order.status != "cancelled"

    total_orders = db.scalar(
        _scoped(select(func.count()).select_from(Order)).where(not_cancelled)
    ) or 0
    pending_orders = db.scalar(
        _scoped(select(func.count()).select_from(Order)).where(
            Order.status == "pending"
        )
    ) or 0
    total_revenue = db.scalar(
        _scoped(select(func.coalesce(func.sum(Order.total_price), 0))).where(
            not_cancelled
        )
    ) or Decimal("0")

    # Peak hours within the same range. Python-side for SQLite/Postgres parity.
    created_times = db.scalars(
        _scoped(select(Order.created_at)).where(not_cancelled)
    ).all()
    orders_by_hour: dict[str, int] = {}
    for ts in created_times:
        if ts is not None:
            hour = f"{ts.hour:02d}"
            orders_by_hour[hour] = orders_by_hour.get(hour, 0) + 1

    return StatsOut(
        total_orders=total_orders,
        total_revenue=Decimal(str(total_revenue)),
        pending_orders=pending_orders,
        orders_by_hour=orders_by_hour,
    )
