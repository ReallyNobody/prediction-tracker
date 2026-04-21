"""Unit tests for the database module's URL handling.

Render (and Heroku before it) hands out bare ``postgres://`` URLs that
SQLAlchemy 2.x refuses. We rewrite those to the explicit psycopg 3 form so
production ``DATABASE_URL`` just works.
"""

from __future__ import annotations

from rmn_dashboard.database import normalize_database_url


def test_sqlite_url_is_passed_through_unchanged() -> None:
    url = "sqlite:///./data/rmn_dashboard.db"
    assert normalize_database_url(url) == url


def test_postgres_scheme_is_rewritten_to_postgresql_psycopg() -> None:
    render_style = "postgres://user:pw@host:5432/dbname"
    assert normalize_database_url(render_style) == "postgresql+psycopg://user:pw@host:5432/dbname"


def test_bare_postgresql_gets_psycopg_driver_appended() -> None:
    url = "postgresql://user:pw@host:5432/dbname"
    assert normalize_database_url(url) == "postgresql+psycopg://user:pw@host:5432/dbname"


def test_explicit_driver_is_preserved() -> None:
    """If the URL already pins a driver, we don't second-guess it."""
    url = "postgresql+psycopg2://user:pw@host:5432/dbname"
    assert normalize_database_url(url) == url

    url = "postgresql+psycopg://user:pw@host:5432/dbname"
    assert normalize_database_url(url) == url
