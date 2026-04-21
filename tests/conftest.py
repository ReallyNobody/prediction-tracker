"""Shared pytest fixtures.

Each test run gets its own in-memory SQLite DB, created from the current
ORM metadata (no Alembic dependency in tests).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rmn_dashboard.models import Base


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """An isolated in-memory SQLite session per test."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
