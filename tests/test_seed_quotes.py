"""Tests for the dev quotes seed CLI.

Same philosophy as ``test_seed_irma`` and ``test_seed_ian``: lock
down the *shape* of what gets written, not the specific synthesized
prices. What matters:

  * One row per universe ticker, all stamped with the same ``as_of``
    so the read-side "latest per ticker" join sees them as a single
    logical scrape.
  * Idempotent — re-running without --clear doesn't insert duplicates.
  * --clear drops every row at SEED_AS_OF and re-inserts cleanly.
  * Deterministic — UVE always gets the same fake price.
  * The service layer accepts seeded rows like real yfinance ones.
"""

from __future__ import annotations

import warnings

from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session

from rmn_dashboard.data.universe import load_universe
from rmn_dashboard.dev.seed_quotes import SEED_AS_OF, _mock_quote_for, seed
from rmn_dashboard.models import TickerQuote
from rmn_dashboard.services.equity_quotes import latest_universe_quotes


def test_seed_inserts_one_row_per_universe_ticker(db_session: Session) -> None:
    summary = seed(db_session)
    db_session.commit()

    universe = load_universe()
    rows = db_session.query(TickerQuote).all()
    assert len(rows) == len(universe.tickers)
    assert summary["rows_inserted"] == len(universe.tickers)

    # Every row stamped with the same as_of — the read-side join needs that.
    # SQLite drops tzinfo on DateTime(timezone=True) round-trip so we
    # compare wall-clock equality regardless of tz preservation
    # (Postgres keeps the tag; SQLite doesn't; both should pass).
    assert len({r.as_of for r in rows}) == 1
    sample_ts = rows[0].as_of
    expected_ts = SEED_AS_OF if sample_ts.tzinfo else SEED_AS_OF.replace(tzinfo=None)
    assert sample_ts == expected_ts
    # Source is "dev-seed" so a developer can audit what came from where.
    assert {r.source for r in rows} == {"dev-seed"}


def test_seed_is_idempotent(db_session: Session) -> None:
    """Running the seed twice should not duplicate rows."""
    seed(db_session)
    db_session.commit()
    seed(db_session)
    db_session.commit()

    universe = load_universe()
    assert db_session.query(TickerQuote).count() == len(universe.tickers)


def test_seed_clear_flag_replaces_seeded_rows(db_session: Session) -> None:
    """--clear drops everything at SEED_AS_OF before re-inserting.

    Verifies expunge_all() is in place so clear+reseed doesn't trip
    the SQLAlchemy identity-map SAWarning (same workaround pattern as
    seed_irma / seed_ian).
    """
    seed(db_session)
    db_session.commit()
    # Mutate so we can prove the clear actually replaced.
    sample = db_session.query(TickerQuote).first()
    sample.last_price = -999.0
    db_session.commit()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        seed(db_session, clear=True)
        db_session.commit()
    identity_warnings = [
        w for w in caught if "identity map already had" in str(w.message)
    ]
    assert not identity_warnings, (
        f"_clear_existing should expunge before re-insert; got: "
        f"{[str(w.message) for w in identity_warnings]}"
    )

    # The mutated -999.0 sentinel is gone.
    assert (
        db_session.query(TickerQuote).filter_by(last_price=-999.0).first() is None
    )


def test_seed_does_not_touch_real_scrape_rows(db_session: Session) -> None:
    """The seed scopes its --clear to SEED_AS_OF only.

    A developer running real ingest jobs against a dev DB shouldn't
    lose their accumulated history when they re-run the seed.
    """
    from datetime import UTC, datetime, timedelta

    real_ts = datetime.now(UTC) - timedelta(days=3)
    db_session.add(
        TickerQuote(ticker="UVE", last_price=99.99, source="yfinance", as_of=real_ts)
    )
    db_session.commit()

    seed(db_session, clear=True)
    db_session.commit()

    # The pre-existing real-scrape row survives.
    surviving = db_session.query(TickerQuote).filter_by(as_of=real_ts).all()
    assert len(surviving) == 1
    assert surviving[0].last_price == 99.99


def test_mock_quote_is_deterministic() -> None:
    """Same ticker → same fake price across calls. Stable dev experience."""
    universe = load_universe()
    uve = next(e for e in universe.tickers if e.ticker == "UVE")
    a = _mock_quote_for(uve)
    b = _mock_quote_for(uve)
    assert a == b


def test_seeded_rows_visible_to_service_layer(db_session: Session) -> None:
    """End-to-end: seed → DB → latest_universe_quotes returns the rows
    with the shape Panel 2 will read from.
    """
    seed(db_session)
    db_session.commit()

    payload = latest_universe_quotes(db_session)
    universe = load_universe()
    assert len(payload) == len(universe.tickers)
    # Every ticker in the seeded run has a non-null quote.
    assert all(row["quote"] is not None for row in payload)
    # The quote dict carries the fields Panel 2 will render.
    sample_quote = payload[0]["quote"]
    assert sample_quote["source"] == "dev-seed"
    assert sample_quote["as_of"] == SEED_AS_OF.isoformat()
