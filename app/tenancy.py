"""
Tenant isolation helpers — the single place that decides "which restaurant
am I acting as?".

Today it returns the hardcoded MVP restaurant. When real auth/subdomains land,
ONLY `current_restaurant_id` changes (read it from the JWT or the Host header)
and every route keeps working, still scoped correctly.

Defense in depth:
- App layer (here + per-query .where(restaurant_id == ...)): always on.
- DB layer (Postgres RLS policies on Neon): added in production so the database
  itself rejects cross-restaurant access even if a query forgets the filter.
"""

from app.config import DEFAULT_RESTAURANT_ID


def current_restaurant_id() -> str:
    """FastAPI dependency: the restaurant the current request acts as.

    Usage:  rid: str = Depends(current_restaurant_id)
    """
    return DEFAULT_RESTAURANT_ID
