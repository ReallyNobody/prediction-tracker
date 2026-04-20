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


def _make_engine() -> Engine:
    """Build the SQLAlchemy engine from settings.

    For the SQLite dev default, this also ensures ``data/`` exists and enables
    the standard ``check_same_thread=False`` flag uvicorn's thread pool needs.
    """
    url = settings.database_url

    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # Ensure the target directory exists for SQLite file URLs.
        # Accepts both "sqlite:///./relative/path.db" and "sqlite:////abs/path.db".
        db_path_str = url.replace("sqlite:///", "", 1)
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False

    return create_engine(
        url,
        echo=settings.debug and settings.env == "development",
        future=True,
        connect_args=connect_args,
    )


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
