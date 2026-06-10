"""
Public customer route: the menu.

    GET /api/menu  ->  every menu item for this restaurant
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DEFAULT_RESTAURANT_ID
from app.db import get_db
from app.models import MenuItem
from app.schemas import MenuItemOut

router = APIRouter(prefix="/api/menu", tags=["menu"])


@router.get("", response_model=list[MenuItemOut])
def list_menu(db: Session = Depends(get_db)):
    """Return all menu items, scoped to the current restaurant."""
    stmt = (
        select(MenuItem)
        .where(MenuItem.restaurant_id == DEFAULT_RESTAURANT_ID)
        .order_by(MenuItem.category, MenuItem.name)
    )
    return db.scalars(stmt).all()
