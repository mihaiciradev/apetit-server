"""
Public table routes — entered via a QR code.

The QR encodes the table's UUID, e.g.  https://<restaurant>/t/{table_id}
NOT the table number. So:
- Knowing "table 4" tells an attacker nothing usable.
- An old link in someone's browser history still resolves to the same table's
  menu, but it carries no order/session state, so nothing breaks and no prior
  guest's order is exposed. (Order access is gated by the unguessable order
  UUID, and the frontend drops it once the order is ready/served.)

    GET  /api/tables/{table_id}             -> resolve a table (number)
    POST /api/tables/{table_id}/checkin     -> mark occupied on scan (10-min window)
    POST /api/tables/{table_id}/call-waiter -> raise a "come over" signal
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ServiceRequest, Table
from app.schemas import ServiceRequestOut, TableOut
from app.tenancy import current_restaurant_id

router = APIRouter(prefix="/api/tables", tags=["tables"])


def _get_table_or_404(db: Session, table_id: str, rid: str) -> Table:
    table = db.get(Table, table_id)
    if table is None or table.restaurant_id != rid:
        raise HTTPException(status_code=404, detail="Table not found")
    return table


@router.get("/{table_id}", response_model=TableOut)
def resolve_table(
    table_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Resolve a scanned QR to a table (so the UI can show 'Table 4').
    This is a pure read — it does NOT change occupancy. Call /checkin for that."""
    return _get_table_or_404(db, table_id, rid)


@router.post("/{table_id}/checkin", response_model=TableOut)
def checkin_table(
    table_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Mark a table occupied because a guest scanned its QR (10-min review
    window). No-op if it's already occupied — we won't downgrade an existing
    'order' occupancy (45 min) back to a 'scan' one. The FE calls this when the
    table menu page loads."""
    table = _get_table_or_404(db, table_id, rid)
    if table.status != "occupied":
        table.status = "occupied"
        table.occupied_at = datetime.now(timezone.utc)
        table.occupied_reason = "scan"
        db.commit()
        db.refresh(table)
    return table


@router.post("/{table_id}/call-waiter", response_model=ServiceRequestOut, status_code=201)
def call_waiter(
    table_id: str,
    db: Session = Depends(get_db),
    rid: str = Depends(current_restaurant_id),
):
    """Guest taps 'Call waiter' — creates an open service request for staff."""
    table = _get_table_or_404(db, table_id, rid)
    req = ServiceRequest(
        restaurant_id=rid,
        type="call_waiter",
        status="open",
        table_id=table.id,
        table_number=table.number,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req
