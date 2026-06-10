"""
Database engine + session setup.

Everything DB-related funnels through here:
- `engine`        : the connection pool to Postgres/SQLite
- `SessionLocal`  : factory that hands out short-lived DB sessions
- `Base`          : the parent class all ORM models inherit from
- `get_db`        : FastAPI dependency that yields a session per request
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

# SQLite needs a special flag because FastAPI may touch the connection from
# different threads. Postgres does not, so we only add it for sqlite URLs.
connect_args = (
    {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Parent class for all ORM models."""
    pass


def get_db():
    """
    Yield a DB session and guarantee it gets closed.

    Used as a FastAPI dependency: `db: Session = Depends(get_db)`.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
