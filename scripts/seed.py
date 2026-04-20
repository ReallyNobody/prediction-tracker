"""Seed the dev database with the preserved cat loss fixture.

Idempotent: safe to re-run. Rows with a matching ``(company, event_name,
filing_date, quarter)`` key are skipped.

Usage::

    python -m scripts.seed

Run after ``alembic upgrade head`` so the ``cat_losses`` table exists.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

# Make ``src/`` importable when running as ``python -m scripts.seed``
# from the project root without the package installed in editable mode.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rmn_dashboard.database import SessionLocal  # noqa: E402
from rmn_dashboard.models import CatLoss  # noqa: E402

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "sample_cat_losses.json"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def seed() -> None:
    data = json.loads(FIXTURE_PATH.read_text())
    inserted = 0
    skipped = 0

    with SessionLocal() as db:
        for row in data:
            # Dedupe on the natural key so re-running is a no-op.
            existing = db.scalar(
                select(CatLoss).where(
                    CatLoss.company == row["company"],
                    CatLoss.event_name == row["event_name"],
                    CatLoss.filing_date == _parse_date(row["filing_date"]),
                    CatLoss.quarter == row.get("quarter"),
                )
            )
            if existing is not None:
                skipped += 1
                continue

            db.add(
                CatLoss(
                    company=row["company"],
                    ticker=row.get("ticker"),
                    filing_type=row["filing_type"],
                    filing_date=_parse_date(row["filing_date"]),
                    quarter=row.get("quarter"),
                    source_accession=row.get("source_accession"),
                    event_name=row["event_name"],
                    event_date=_parse_date(row.get("event_date")),
                    gross_loss_usd=row.get("gross_loss_usd"),
                    net_loss_usd=row.get("net_loss_usd"),
                    loss_type=row.get("loss_type"),
                    geography=row.get("geography"),
                    context=row.get("context"),
                )
            )
            inserted += 1

        db.commit()

    print(f"Seed complete: {inserted} inserted, {skipped} already present.")


if __name__ == "__main__":
    seed()
