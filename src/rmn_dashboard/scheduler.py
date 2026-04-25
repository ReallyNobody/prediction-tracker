"""Background scheduler for periodic data-ingest jobs.

We use APScheduler's ``BackgroundScheduler`` — runs in a thread inside the
web process, shares the app's ``SessionLocal`` and settings. The scheduler
is opt-in via ``settings.scheduler_enabled`` (False in dev, True in prod
via env var).

Deployment contract: **one web worker**. Gunicorn with multiple workers
would spawn one scheduler per worker and double-fire every ingest. Render's
Starter plan runs a single worker by default; we do not override that. If
we ever need multiple web workers, the scheduler moves to a separate
Render background-worker service and this module becomes scheduler-only
(the FastAPI lifespan hook drops the ``scheduler.start()`` call).

Error isolation: the job wrapper catches and logs every exception. The
operational posture is "next tick will retry" — a caught failure is
cheaper than bringing down the scheduler thread or spamming error
listeners. The Kalshi scraper's per-series try/except already handles
transient rate-limit and network failures inside a single run; this is
the outermost belt-and-suspenders layer.

Tested via ``tests/test_scheduler.py`` — we never actually start the
scheduler in tests (real-time wall clock behavior is APScheduler's
problem, not ours). We do assert: the job is registered with the correct
trigger, the ``run_on_start`` flag pins ``next_run_time`` to now, the job
wrapper swallows exceptions, and the FastAPI lifespan hook starts/stops
the scheduler only when ``scheduler_enabled`` is True.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from rmn_dashboard.database import SessionLocal
from rmn_dashboard.tasks.ingest_kalshi import run_kalshi_ingest
from rmn_dashboard.tasks.ingest_nhc import run_nhc_ingest
from rmn_dashboard.tasks.ingest_nhc_forecasts import run_nhc_forecast_ingest
from rmn_dashboard.tasks.ingest_yfinance import run_yfinance_ingest

logger = logging.getLogger(__name__)

KALSHI_JOB_ID = "kalshi_ingest"
NHC_JOB_ID = "nhc_ingest"
NHC_FORECAST_JOB_ID = "nhc_forecast_ingest"
YFINANCE_JOB_ID = "yfinance_ingest"


def _run_kalshi_ingest_job() -> None:
    """APScheduler job wrapper: open a session, run the ingest, swallow errors.

    This function must never raise out to APScheduler. A caught, logged
    exception keeps the scheduler thread healthy and lets the next tick
    take its shot. Use :func:`run_kalshi_ingest` directly (with a session
    you own) when you want exceptions to propagate — e.g. in tests or from
    the ``python -m rmn_dashboard.tasks.ingest_kalshi`` CLI.
    """
    db = SessionLocal()
    try:
        count = run_kalshi_ingest(db)
        logger.info("Scheduled Kalshi ingest persisted %d rows", count)
    except Exception:  # noqa: BLE001 — intentional blanket catch; see module docstring
        logger.exception("Scheduled Kalshi ingest failed; will retry next tick")
    finally:
        db.close()


def _run_nhc_ingest_job() -> None:
    """APScheduler job wrapper for the NHC active-storms ingest.

    Same contract as :func:`_run_kalshi_ingest_job` — never raises out to
    APScheduler, always closes its session. Off-season ``count == 0`` is
    a normal state and logged at INFO via the task itself; we don't need
    to duplicate that here.
    """
    db = SessionLocal()
    try:
        count = run_nhc_ingest(db)
        logger.info("Scheduled NHC ingest persisted %d rows", count)
    except Exception:  # noqa: BLE001 — intentional blanket catch; see module docstring
        logger.exception("Scheduled NHC ingest failed; will retry next tick")
    finally:
        db.close()


def _run_nhc_forecast_ingest_job() -> None:
    """APScheduler job wrapper for the NHC forecast-product ingest.

    Same contract as the other job wrappers: never raises out to
    APScheduler, always closes its session. Off-season ``count == 0``
    (no active storms) is the normal steady state and is logged at INFO
    by the task itself.
    """
    db = SessionLocal()
    try:
        count = run_nhc_forecast_ingest(db)
        logger.info("Scheduled NHC forecast ingest persisted %d rows", count)
    except Exception:  # noqa: BLE001 — intentional blanket catch; see module docstring
        logger.exception("Scheduled NHC forecast ingest failed; will retry next tick")
    finally:
        db.close()


def _run_yfinance_ingest_job() -> None:
    """APScheduler job wrapper for the hurricane-universe equity-quote ingest.

    Same contract: never raises out to APScheduler, always closes its
    session. yfinance hiccups are common (Yahoo periodically rotates
    internal endpoints), and the per-ticker log-and-skip inside the
    scraper means a partial scrape still persists what came back.
    Outer ``except`` here is the belt-and-suspenders layer for
    catastrophic failures (e.g. yfinance import error from a broken
    venv, or a SQLAlchemy bulk insert that hits a constraint).
    """
    db = SessionLocal()
    try:
        count = run_yfinance_ingest(db)
        logger.info("Scheduled yfinance ingest persisted %d rows", count)
    except Exception:  # noqa: BLE001 — intentional blanket catch; see module docstring
        logger.exception("Scheduled yfinance ingest failed; will retry next tick")
    finally:
        db.close()


def build_scheduler(
    kalshi_interval_minutes: int,
    nhc_interval_minutes: int,
    nhc_forecast_interval_minutes: int,
    yfinance_interval_minutes: int,
    *,
    kalshi_job: Callable[[], None] = _run_kalshi_ingest_job,
    nhc_job: Callable[[], None] = _run_nhc_ingest_job,
    nhc_forecast_job: Callable[[], None] = _run_nhc_forecast_ingest_job,
    yfinance_job: Callable[[], None] = _run_yfinance_ingest_job,
    run_on_start: bool = True,
) -> BackgroundScheduler:
    """Construct (but do not start) a ``BackgroundScheduler`` with all ingest jobs.

    ``run_on_start=True`` pins first fire to "now" for every job so fresh
    deploys populate every panel without waiting a full interval. Tests
    pass ``run_on_start=False`` so jobs don't try to fire while the test
    process is assembling fixtures.

    Each ``*_job`` kwarg is injectable so callers (and tests) can swap in
    a different callable without patching module globals.

    Intervals are separate parameters (even though prod defaults Kalshi
    and NHC-observations both to 15 min) so ops can tune each source's
    cadence independently — NHC has no rate limit but Kalshi does, and
    NHC forecast shapefiles only republish on advisory boundaries so a
    longer cadence there conserves bandwidth.
    """
    scheduler = BackgroundScheduler(timezone="UTC")

    # Shared job-registration kwargs.
    # ``max_instances=1``: if a run outruns the interval, skip the next
    # tick rather than overlap — prevents double-snapshotting.
    # ``coalesce=True``: after downtime, collapse any backlog into a
    # single fire instead of replaying every missed tick.
    now = datetime.now(UTC) if run_on_start else None

    scheduler.add_job(
        kalshi_job,
        trigger=IntervalTrigger(minutes=kalshi_interval_minutes),
        id=KALSHI_JOB_ID,
        name="Kalshi hurricane markets ingest",
        next_run_time=now,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        nhc_job,
        trigger=IntervalTrigger(minutes=nhc_interval_minutes),
        id=NHC_JOB_ID,
        name="NHC active-storms ingest",
        next_run_time=now,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        nhc_forecast_job,
        trigger=IntervalTrigger(minutes=nhc_forecast_interval_minutes),
        id=NHC_FORECAST_JOB_ID,
        name="NHC forecast-product ingest",
        next_run_time=now,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        yfinance_job,
        trigger=IntervalTrigger(minutes=yfinance_interval_minutes),
        id=YFINANCE_JOB_ID,
        name="yfinance hurricane-universe equity-quote ingest",
        next_run_time=now,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
