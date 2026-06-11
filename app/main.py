"""
APETIT backend — FastAPI entry point.

Responsibilities:
- create the app + CORS (so the Next.js frontends can call us)
- create DB tables on startup (fine for the MVP; use migrations later)
- mount the feature routers (menu, orders, kitchen, admin)

Run locally:   uvicorn app.main:app --reload
Interactive docs:   http://127.0.0.1:8000/docs
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.db import Base, SessionLocal, engine
from app.models import AuditLog
from app.routers import (
    admin,
    kitchen,
    menu,
    orders,
    reservations,
    staff,
    tables,
)

audit_logger = logging.getLogger("apetit.audit")

# Only state-changing verbs are worth auditing (reads are noise).
_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: create any tables that don't exist yet.
    # (For production, swap this for Alembic migrations.)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="APETIT Backend", version="0.1.0", lifespan=lifespan)

# Allow the frontend dev server + Vercel deployments to call the API.
# Tighten `allow_origins` to your real domains before production.
app.add_middleware(
    CORSMiddleware,
    # NOTE: browsers reject `allow_origins=["*"]` together with
    # allow_credentials=True. The production frontend domain comes from
    # FRONTEND_URL (set it as a Fly env var); any localhost port is allowed
    # in dev via the regex.
    allow_origins=[config.FRONTEND_URL] if config.FRONTEND_URL else [],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],   # incl. PATCH / DELETE
    # Explicit headers + the custom screen header the FE sends for audit logs.
    allow_headers=["*", "X-Client-Screen"],
)


@app.middleware("http")
async def audit_log_middleware(request: Request, call_next):
    """Record every successful mutating request to the audit log.

    The FE should send an `X-Client-Screen` header (e.g. "admin", "kitchen",
    "customer-menu", "table") so each entry says where it came from. Failures
    here never affect the response — logging is best-effort.
    """
    response = await call_next(request)
    if request.method in _AUDIT_METHODS and response.status_code < 400:
        try:
            db = SessionLocal()
            try:
                db.add(
                    AuditLog(
                        restaurant_id=config.DEFAULT_RESTAURANT_ID,
                        source=request.headers.get("x-client-screen", "unknown"),
                        method=request.method,
                        path=request.url.path,
                        status_code=response.status_code,
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001 — never break the request over a log
            audit_logger.exception("audit log write failed")
    return response


# Feature routers.
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(tables.router)
app.include_router(reservations.router)
app.include_router(kitchen.router)
app.include_router(staff.router)
app.include_router(admin.router)


@app.get("/")
def read_root():
    return {"message": "Hello from APETIT Backend"}


@app.get("/health")
def health():
    return {"status": "ok"}
