"""
Public customer routes: placing, tracking, and closing out an order.

    POST /api/orders                  -> create an order (table or web)
    GET  /api/orders/{order_id}       -> check status + items (acts as the
                                         guest's access token: the UUID is
                                         unguessable)
    POST /api/orders/{order_id}/request-bill
                                      -> only once SERVED: ask for the check

Rules:
- The client only sends item ids + quantities. Prices and totals are computed
  server-side. Never trust a price from the browser.
- Ordering from a table QR -> send table_id, order_type is forced dine_in.
- Ordering from the web    -> omit table_id, must send order_type
  (dine_in | takeaway). Payment is always handled in person.
"""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MenuItem, Order, OrderItem, ServiceRequest, Table
from app.schemas import OrderCreate, OrderOut
from app.tenancy import current_restaurant_id

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _next_daily_number(db: Session, restaurant_id: str) -> int:
    """The next per-restaurant, per-day order counter (1, 2, 3...).

    Counts orders already created today (UTC) for this restaurant and adds 1,
    so the sequence naturally resets at midnight. Fine for the MVP; under heavy
    concurrency you'd want a DB sequence or a unique (restaurant, day, n) index.
    """
    today = datetime.now(timezone.utc).date()
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    count_today = db.scalar(
        select(func.count())
        .select_from(Order)
        .where(
            Order.restaurant_id == restaurant_id,
            Order.created_at >= start,
        )
    ) or 0
    return count_today + 1


@router.post("", response_model=OrderOut, status_code=201)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Create a new order from a list of (menu_item_id, quantity)."""
    # 1. Resolve dine-in vs takeaway and the (optional) table.
    table_id = None
    table = None
    if payload.table_id:
        table = db.get(Table, payload.table_id)
        if table is None or table.restaurant_id != rid:
            raise HTTPException(status_code=404, detail="Table not found")
        table_id = table.id
        order_type = "dine_in"  # a table order is always dine-in
    else:
        if payload.order_type is None:
            raise HTTPException(
                status_code=422,
                detail="order_type (dine_in | takeaway) is required when "
                       "ordering without a table.",
            )
        order_type = payload.order_type

    # 2. Fetch all referenced menu items in one query, scoped to this tenant.
    requested_ids = {line.menu_item_id for line in payload.items}
    stmt = select(MenuItem).where(
        MenuItem.id.in_(requested_ids),
        MenuItem.restaurant_id == rid,
    )
    menu_by_id = {item.id: item for item in db.scalars(stmt).all()}

    missing = requested_ids - menu_by_id.keys()
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown menu item(s): {', '.join(missing)}",
        )

    # Block ordering items that staff have "86'd" (marked unavailable).
    unavailable = [m.name for m in menu_by_id.values() if not m.available]
    if unavailable:
        raise HTTPException(
            status_code=409,
            detail=f"Currently unavailable: {', '.join(unavailable)}",
        )

    # 3. Build line items with server-side prices and sum the total.
    order = Order(
        restaurant_id=rid,
        daily_number=_next_daily_number(db, rid),
        status="pending",
        order_type=order_type,
        table_id=table_id,
        total_price=Decimal("0"),
    )
    total = Decimal("0")
    for line in payload.items:
        menu_item = menu_by_id[line.menu_item_id]
        price = Decimal(str(menu_item.price))
        total += price * line.quantity
        order.items.append(
            OrderItem(
                menu_item_id=menu_item.id,
                menu_item_name=menu_item.name,          # snapshot for tickets/history
                menu_item_category=menu_item.category,  # snapshot (bar/KDS grouping)
                quantity=line.quantity,
                price_at_time=price,
                special_requests=line.special_requests,
            )
        )
    order.total_price = total

    # A dine-in order means the table is in use — mark it occupied.
    if table_id is not None and table is not None and table.status != "occupied":
        table.status = "occupied"
        table.occupied_at = datetime.now(timezone.utc)

    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Return a single order's status and items."""
    order = db.get(Order, order_id)
    if order is None or order.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/{order_id}/request-bill", response_model=OrderOut)
def request_bill(
    order_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Guest asks for the check. Allowed only once the order is SERVED.
    Payment method (cash/card) is intentionally NOT collected here — that's
    sorted in person with the waiter."""
    order = db.get(Order, order_id)
    if order is None or order.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "served":
        raise HTTPException(
            status_code=409,
            detail="The bill can only be requested after the order is served.",
        )

    order.bill_requested = True
    # Drop a signal on the staff dashboard.
    db.add(
        ServiceRequest(
            restaurant_id=rid,
            type="bill",
            status="open",
            table_id=order.table_id,
            table_number=order.table.number if order.table else None,
            order_id=order.id,
        )
    )
    db.commit()
    db.refresh(order)
    return order
