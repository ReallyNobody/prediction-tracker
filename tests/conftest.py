"""Shared pytest fixtures.

Each test gets a fresh in-memory SQLite database with the full ORM schema
applied (no Alembic dependency in tests — we create from ``Base.metadata``
so schema drift is caught by unit tests on the models, not by migration
playback).

The ``client`` fixture is what HTTP tests should use — it overrides
``get_session`` on the running FastAPI app so the endpoint layer talks
to the same in-memory DB as the raw session fixture. Without this, CI
runs against the bare module engine (whatever ``DATABASE_URL`` happens
to point at, typically an un-migrated file) and any route that queries
a real table blows up with ``no such table``.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from rmn_dashboard.database import get_session
from rmn_dashboard.main import app
from rmn_dashboard.models import Base


@pytest.fixture
def _test_engine() -> Generator[Engine, None, None]:
    """In-memory SQLite engine with the full schema, scoped to one test.

    Uses ``StaticPool`` so every connection checked out of the pool shares
    the same underlying SQLite in-memory database. Without this, FastAPI's
    TestClient can open a fresh connection for a route handler that sees
    an empty database (each new connection to ``:memory:`` creates its own
    DB by default), and any table we created during setup "disappears".
    """
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(_test_engine: Engine) -> Generator[Session, None, None]:
    """Plain SQLAlchemy session for model-level tests."""
    TestingSession = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False, future=True)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(_test_engine: Engine) -> Generator[TestClient, None, None]:
    """FastAPI TestClient wired to the in-memory DB via dependency override."""
    TestingSession = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False, future=True)

    def _override_get_session() -> Generator[Session, None, None]:
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_get_session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
