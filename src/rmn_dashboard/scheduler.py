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

logger = logging.getLogger(__name__)

KALSHI_JOB_ID = "kalshi_ingest"


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


def build_scheduler(
    interval_minutes: int,
    *,
    job: Callable[[], None] = _run_kalshi_ingest_job,
    run_on_start: bool = True,
) -> BackgroundScheduler:
    """Construct (but do not start) a ``BackgroundScheduler`` with the ingest job.

    ``run_on_start=True`` pins the first fire time to "now" so fresh deploys
    don't wait a full interval to populate the Markets panel. Tests pass
    ``run_on_start=False`` so the job doesn't try to run while the test
    process is assembling fixtures.

    ``job`` is injectable so callers (and tests) can swap in a different
    callable without patching module globals.
    """
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id=KALSHI_JOB_ID,
        name="Kalshi hurricane markets ingest",
        next_run_time=datetime.now(UTC) if run_on_start else None,
        # max_instances=1: if a run takes longer than the interval, skip
        # the next tick rather than overlap. Overlapping runs could
        # double-post snapshots and contend for the Kalshi rate limiter.
        max_instances=1,
        # coalesce=True: after downtime (deploy restart, laptop sleep),
        # collapse any backlog into a single fire instead of firing
        # sequentially for every missed tick.
        coalesce=True,
    )
    return scheduler
