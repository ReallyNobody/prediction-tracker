"""SQLAlchemy engine and session factory.

Sync SQLAlchemy 2.x is used throughout the app — async would be nice later,
but simplicity matters more than marginal throughput for the MVP.

Consumers should use ``get_session`` as a FastAPI dependency:

    from fastapi import Depends
    from sqlalchemy.orm import Session
    from rmn_dashboard.database import get_session

    @router.get("/...")
    def handler(db: Session = Depends(get_session)):
        ...
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from rmn_dashboard.config import PROJECT_ROOT, settings


def normalize_database_url(url: str) -> str:
    """Normalize provider-specific URL quirks into a SQLAlchemy-friendly form.

    Render (and Heroku before it) hands out Postgres URLs with a bare
    ``postgres://`` scheme. SQLAlchemy 2.x dropped support for that alias and
    expects ``postgresql://`` (or an explicit driver like
    ``postgresql+psycopg://`` for psycopg 3). We rewrite it here so callers can
    paste the Render-supplied value straight into ``DATABASE_URL`` without
    having to remember the gotcha.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        # Force psycopg 3 driver — matches what we install in the prod extras.
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _make_engine() -> Engine:
    """Build the SQLAlchemy engine from settings.

    For the SQLite dev default, this also ensures ``data/`` exists and enables
    the standard ``check_same_thread=False`` flag uvicorn's thread pool needs.
    For Postgres (Render prod), we normalize the URL scheme so SQLAlchemy 2.x
    accepts it and set a small connection pool suitable for a Starter dyno.
    """
    url = normalize_database_url(settings.database_url)

    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {
        "echo": settings.debug and settings.env == "development",
        "future": True,
    }

    if url.startswith("sqlite"):
        # Ensure the target directory exists for SQLite file URLs.
        # Accepts both "sqlite:///./relative/path.db" and "sqlite:////abs/path.db".
        db_path_str = url.replace("sqlite:///", "", 1)
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
    elif url.startswith("postgresql"):
        # Render's Starter Postgres caps at ~97 connections. Keep the pool
        # conservative so multiple web workers + the scheduler don't exhaust it.
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 5
        engine_kwargs["pool_pre_ping"] = True  # dodge stale-connection errors
        engine_kwargs["pool_recycle"] = 1800  # recycle every 30 min

    return create_engine(url, connect_args=connect_args, **engine_kwargs)


engine: Engine = _make_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def get_session() -> Generator[Session, None, None]:
    """Yield a database session, ensuring it's always closed.

    Designed for FastAPI ``Depends()`` injection. Commits are the caller's
    responsibility; this helper only manages lifecycle.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
