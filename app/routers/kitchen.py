"""
Kitchen / floor status routes (no auth yet in the MVP).

    GET   /api/kitchen/orders               -> active orders (not served)
    PATCH /api/kitchen/orders/{id}/status   -> advance an order's status

Lifecycle the frontend colour-codes by "who must act":
    pending   -> waiter must confirm with the guests
    confirmed -> kitchen must cook
    ready     -> waiter must deliver
    served    -> done (drops off this list)

Marking "served" stamps completed_at; moving back off "served" clears it.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Order
from app.schemas import OrderOut, OrderStatusUpdate
from app.tenancy import current_restaurant_id

router = APIRouter(prefix="/api/kitchen", tags=["kitchen"])


@router.get("/orders", response_model=list[OrderOut])
def kitchen_orders(
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """All not-yet-served orders, ordered by today's order number."""
    stmt = (
        select(Order)
        .where(
            Order.restaurant_id == rid,
            Order.status.notin_(("served", "cancelled")),
        )
        .order_by(Order.created_at)
    )
    return db.scalars(stmt).all()


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
def update_status(
    order_id: str,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Move an order along its lifecycle
    (pending -> confirmed -> ready -> served)."""
    order = db.get(Order, order_id)
    if order is None or order.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = payload.status
    # Stamp completion time for terminal states (served / cancelled).
    order.completed_at = (
        datetime.now(timezone.utc)
        if payload.status in ("served", "cancelled")
        else None
    )

    db.commit()
    db.refresh(order)
    return order
