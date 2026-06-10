"""
Staff floor routes (no auth yet in the MVP) — the waiter's view of pending
service requests (call-waiter + bill).

    GET   /api/staff/service-requests            -> open requests, oldest first
    PATCH /api/staff/service-requests/{id}/resolve -> mark one handled
"""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Order, Reservation, ServiceRequest, Table, Voucher
from app.notifications import send_reservation_confirmed
from app.schemas import (
    ReservationOut,
    ReservationStatusUpdate,
    ReservationTableAssign,
    ServiceRequestOut,
    StaffSummaryOut,
    TableOut,
    TableStatusUpdate,
    VoucherOut,
    VoucherRedeem,
)
from app.tenancy import current_restaurant_id


def _validate_table(db: Session, table_id: str, rid: str) -> None:
    """Raise 404 unless the table exists and belongs to this restaurant."""
    table = db.get(Table, table_id)
    if table is None or table.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Table not found")

router = APIRouter(prefix="/api/staff", tags=["staff"])


@router.get("/summary", response_model=StaffSummaryOut)
def staff_summary(
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """One-shot counts for tab badges — replaces polling 4 separate lists."""

    def _count(model, *conditions):
        return db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.restaurant_id == rid, *conditions)
        ) or 0

    return StaffSummaryOut(
        pending_orders=_count(Order, Order.status == "pending"),
        open_service_requests=_count(ServiceRequest, ServiceRequest.status == "open"),
        pending_reservations=_count(Reservation, Reservation.status == "pending"),
        occupied_tables=_count(Table, Table.status == "occupied"),
    )


@router.get("/service-requests", response_model=list[ServiceRequestOut])
def open_requests(
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """All unresolved call-waiter / bill requests, oldest first."""
    stmt = (
        select(ServiceRequest)
        .where(
            ServiceRequest.restaurant_id == rid,
            ServiceRequest.status == "open",
        )
        .order_by(ServiceRequest.created_at)
    )
    return db.scalars(stmt).all()


@router.patch("/service-requests/{request_id}/resolve", response_model=ServiceRequestOut)
def resolve_request(
    request_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Mark a service request handled."""
    req = db.get(ServiceRequest, request_id)
    if req is None or req.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Service request not found")
    req.status = "resolved"
    req.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)
    return req


# ─────────────────────────── Table management (staff) ───────────────────────────

@router.get("/tables", response_model=list[TableOut])
def staff_tables(
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """All tables with their free/occupied status, ordered by number."""
    stmt = (
        select(Table)
        .where(Table.restaurant_id == rid)
        .order_by(Table.number)
    )
    return db.scalars(stmt).all()


@router.patch("/tables/{table_id}/status", response_model=TableOut)
def set_table_status(
    table_id: str,
    payload: TableStatusUpdate,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Mark a table free/occupied manually (e.g. a walk-in not ordered via the
    app). No order data is required or attached."""
    table = db.get(Table, table_id)
    if table is None or table.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Table not found")
    table.status = payload.status
    db.commit()
    db.refresh(table)
    return table


# ─────────────────────────── Reservations (staff) ───────────────────────────

@router.get("/reservations", response_model=list[ReservationOut])
def staff_reservations(
    status: str | None = Query(
        default=None,
        description="Optional filter: pending | confirmed | declined | cancelled",
    ),
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Reservations for staff to review, soonest first. Optionally filter by
    status (default: all)."""
    stmt = select(Reservation).where(Reservation.restaurant_id == rid)
    if status:
        stmt = stmt.where(Reservation.status == status)
    stmt = stmt.order_by(Reservation.date, Reservation.time)
    return db.scalars(stmt).all()


@router.patch("/reservations/{reservation_id}/status", response_model=ReservationOut)
def set_reservation_status(
    reservation_id: str,
    payload: ReservationStatusUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Staff approve (confirmed) or reject (declined) a booking request."""
    r = db.get(Reservation, reservation_id)
    if r is None or r.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Reservation not found")

    # Optionally assign a table in the same call (common on approval).
    if payload.table_id is not None:
        _validate_table(db, payload.table_id, rid)
        r.table_id = payload.table_id

    r.status = payload.status
    db.commit()
    db.refresh(r)

    if payload.status == "confirmed":
        background_tasks.add_task(
            send_reservation_confirmed,
            r.customer_email,
            r.customer_name,
            r.date,
            r.time,
            r.party_size,
            r.id,
        )
    return r


@router.patch("/reservations/{reservation_id}/table", response_model=ReservationOut)
def assign_reservation_table(
    reservation_id: str,
    payload: ReservationTableAssign,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Assign / reassign (or clear) the table on a reservation, independent of
    its approval status."""
    r = db.get(Reservation, reservation_id)
    if r is None or r.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if payload.table_id is not None:
        _validate_table(db, payload.table_id, rid)
    r.table_id = payload.table_id  # may be None to clear
    db.commit()
    db.refresh(r)
    return r


# ─────────────────────────── Vouchers (staff subscreen) ───────────────────────────

@router.get("/vouchers", response_model=list[VoucherOut])
def list_vouchers(
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """All vouchers, newest first (for the voucher subscreen)."""
    stmt = (
        select(Voucher)
        .where(Voucher.restaurant_id == rid)
        .order_by(Voucher.created_at.desc())
    )
    return db.scalars(stmt).all()


@router.get("/vouchers/{code}", response_model=VoucherOut)
def lookup_voucher(
    code: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Look up a voucher by code WITHOUT redeeming it (preview before applying)."""
    v = db.scalar(
        select(Voucher).where(
            Voucher.restaurant_id == rid,
            Voucher.code == code.strip().upper(),
        )
    )
    if v is None:
        raise HTTPException(status_code=404, detail="Voucher not found")
    return v


@router.post("/vouchers/redeem", response_model=VoucherOut)
def redeem_voucher(
    payload: VoucherRedeem,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Validate + consume a voucher code. One-time use."""
    v = db.scalar(
        select(Voucher).where(
            Voucher.restaurant_id == rid,
            Voucher.code == payload.code.strip().upper(),
        )
    )
    if v is None:
        raise HTTPException(status_code=404, detail="Voucher not found")
    if v.status == "redeemed":
        raise HTTPException(status_code=409, detail="Voucher already redeemed")

    v.status = "redeemed"
    v.redeemed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(v)
    return v
