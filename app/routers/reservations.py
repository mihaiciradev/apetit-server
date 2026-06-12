"""
Public reservation routes (guest-facing).

    GET  /api/reservations/availability?date=YYYY-MM-DD
                                        -> per-slot list of free tables
    POST /api/reservations              -> request a booking (status=pending)
    GET  /api/reservations/{id}         -> view a booking (UUID = access token)
    POST /api/reservations/{id}/cancel  -> guest cancels (no modify in MVP)

Availability is advisory: a table is shown as taken for a slot only if it
already has a pending/confirmed reservation for that exact date+slot. Staff
still approve or decline every request, so we don't model real capacity.
"""

from datetime import date as date_type

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import RESERVATION_SLOTS
from app.db import get_db
from app.models import Reservation
from app.notifications import send_reservation_received
from app.schemas import (
    AvailabilityOut,
    ReservationCreate,
    ReservationOut,
    SlotAvailability,
)
from app.tenancy import current_restaurant_id

router = APIRouter(prefix="/api/reservations", tags=["reservations"])


@router.get("/availability", response_model=AvailabilityOut)
def availability(
    date: date_type = Query(..., description="YYYY-MM-DD"),
    rid: str = Depends(current_restaurant_id),
):
    """List the bookable time slots for a date.

    MVP: every slot is offered (`available=true`) — staff approve or decline
    each request, so we don't model real table capacity here. The endpoint
    still exists (and returns a per-slot flag) so capacity rules can be added
    later without changing the frontend contract.
    """
    slots = [SlotAvailability(time=slot, available=True) for slot in RESERVATION_SLOTS]
    return AvailabilityOut(date=date, slots=slots)


@router.post("", response_model=ReservationOut, status_code=201)
def create_reservation(
    payload: ReservationCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Request a booking. Starts as pending until staff approve.
    Guests do NOT pick a table — staff assign one on approval if needed."""
    if payload.time not in RESERVATION_SLOTS:
        raise HTTPException(
            status_code=422,
            detail=f"time must be one of {RESERVATION_SLOTS}",
        )

    reservation = Reservation(
        restaurant_id=rid,
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
        customer_phone=payload.customer_phone,
        party_size=payload.party_size,
        date=payload.date,
        time=payload.time,
        table_id=None,           # assigned later by staff, not the guest
        status="pending",
        special_notes=payload.special_notes,
    )
    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    request.state.audit_detail = (
        f"requested a reservation for {reservation.customer_name} "
        f"({reservation.date} {reservation.time}, party of {reservation.party_size})"
    )

    # Send the "we received your request" email after the response, with plain
    # values (the session is closed by the time this runs).
    background_tasks.add_task(
        send_reservation_received,
        reservation.customer_email,
        reservation.customer_name,
        reservation.date,
        reservation.time,
        reservation.party_size,
        reservation.id,
    )
    return reservation


@router.get("/{reservation_id}", response_model=ReservationOut)
def get_reservation(
    reservation_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """View a single reservation (the UUID acts as the guest's access token)."""
    r = db.get(Reservation, reservation_id)
    if r is None or r.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return r


@router.post("/{reservation_id}/cancel", response_model=ReservationOut)
def cancel_reservation(
    reservation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Guest cancels their booking. MVP allows cancel only (no modify)."""
    r = db.get(Reservation, reservation_id)
    if r is None or r.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if r.status in ("declined", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Reservation is already {r.status}.",
        )

    r.status = "cancelled"
    db.commit()
    db.refresh(r)
    request.state.audit_detail = (
        f"cancelled reservation for {r.customer_name} ({r.date} {r.time})"
    )
    return r
