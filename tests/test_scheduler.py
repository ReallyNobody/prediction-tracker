"""Tests for the BackgroundScheduler wiring.

We never actually start the scheduler — real-time firing is APScheduler's
problem, not ours. These tests assert:

  * ``build_scheduler`` registers the Kalshi ingest job with the correct
    trigger and guard flags (max_instances=1, coalesce=True).
  * The ``run_on_start`` flag pins ``next_run_time`` to now when set, and
    leaves it unset when not.
  * ``_run_kalshi_ingest_job`` swallows exceptions (so the scheduler thread
    stays healthy) and always closes its DB session.
  * The FastAPI lifespan hook starts the scheduler only when
    ``settings.scheduler_enabled`` is True, and shuts it down on exit.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from rmn_dashboard.scheduler import (
    KALSHI_JOB_ID,
    _run_kalshi_ingest_job,
    build_scheduler,
)

# ----- build_scheduler -----------------------------------------------------


def test_build_scheduler_registers_kalshi_ingest_job() -> None:
    # Scheduler is never started; no teardown needed (APScheduler would raise
    # SchedulerNotRunningError on shutdown of an unstarted scheduler).
    scheduler = build_scheduler(interval_minutes=15, run_on_start=False)
    job = scheduler.get_job(KALSHI_JOB_ID)
    assert job is not None
    # APScheduler stores the trigger's interval as a timedelta.
    assert job.trigger.interval.total_seconds() == 15 * 60
    assert job.max_instances == 1
    assert job.coalesce is True


def test_build_scheduler_run_on_start_pins_immediate_first_run() -> None:
    scheduler = build_scheduler(interval_minutes=15, run_on_start=True)
    job = scheduler.get_job(KALSHI_JOB_ID)
    assert job.next_run_time is not None


def test_build_scheduler_without_run_on_start_leaves_next_run_unset() -> None:
    scheduler = build_scheduler(interval_minutes=15, run_on_start=False)
    job = scheduler.get_job(KALSHI_JOB_ID)
    # With run_on_start=False and the scheduler not started, the job has no
    # pinned next_run_time — APScheduler would compute one on .start().
    assert job.next_run_time is None


def test_build_scheduler_accepts_injectable_job() -> None:
    """The ``job`` kwarg is the seam callers use to swap in a test double
    without monkey-patching module globals."""
    calls: list[int] = []

    def fake_job() -> None:
        calls.append(1)

    scheduler = build_scheduler(interval_minutes=1, job=fake_job, run_on_start=False)
    job = scheduler.get_job(KALSHI_JOB_ID)
    # Invoke the stored callable directly — no threads, no wall clock.
    job.func()
    assert calls == [1]


# ----- _run_kalshi_ingest_job ---------------------------------------------


def test_job_wrapper_logs_success_count(caplog: pytest.LogCaptureFixture) -> None:
    session = MagicMock()
    with (
        patch("rmn_dashboard.scheduler.SessionLocal", return_value=session),
        patch("rmn_dashboard.scheduler.run_kalshi_ingest", return_value=17),
        caplog.at_level(logging.INFO, logger="rmn_dashboard.scheduler"),
    ):
        _run_kalshi_ingest_job()

    assert any("persisted 17 rows" in r.message for r in caplog.records), caplog.text
    session.close.assert_called_once()


def test_job_wrapper_swallows_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    """Any exception inside the ingest must be caught and logged — never
    propagated to APScheduler, where it would pollute the job log and
    potentially trip error listeners."""
    session = MagicMock()
    with (
        patch("rmn_dashboard.scheduler.SessionLocal", return_value=session),
        patch(
            "rmn_dashboard.scheduler.run_kalshi_ingest",
            side_effect=RuntimeError("kalshi exploded"),
        ),
        caplog.at_level(logging.ERROR, logger="rmn_dashboard.scheduler"),
    ):
        # Must not raise.
        _run_kalshi_ingest_job()

    assert any("failed; will retry next tick" in r.message for r in caplog.records)
    session.close.assert_called_once()  # session still closed on the error path


def test_job_wrapper_closes_session_on_success() -> None:
    session = MagicMock()
    with (
        patch("rmn_dashboard.scheduler.SessionLocal", return_value=session),
        patch("rmn_dashboard.scheduler.run_kalshi_ingest", return_value=0),
    ):
        _run_kalshi_ingest_job()
    session.close.assert_called_once()


# ----- FastAPI lifespan integration ---------------------------------------


def test_lifespan_does_not_start_scheduler_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default config has scheduler_enabled=False; the lifespan hook must not
    build or start a scheduler in that case. This is what keeps uvicorn
    --reload and the test suite quiet."""
    from rmn_dashboard import main as main_module

    monkeypatch.setattr(main_module.settings, "scheduler_enabled", False, raising=False)

    with patch("rmn_dashboard.main.build_scheduler") as builder:
        with TestClient(main_module.app):
            pass  # TestClient context runs the lifespan
        builder.assert_not_called()


def test_lifespan_starts_and_stops_scheduler_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With scheduler_enabled=True the lifespan must build, start, then shut
    down the scheduler — once each — using the configured interval."""
    from rmn_dashboard import main as main_module

    monkeypatch.setattr(main_module.settings, "scheduler_enabled", True, raising=False)
    monkeypatch.setattr(main_module.settings, "kalshi_ingest_interval_minutes", 15, raising=False)

    fake_scheduler = MagicMock()
    with patch("rmn_dashboard.main.build_scheduler", return_value=fake_scheduler) as builder:
        with TestClient(main_module.app):
            # Startup ran: builder called, scheduler started, but not yet stopped.
            builder.assert_called_once_with(15)
            fake_scheduler.start.assert_called_once()
            fake_scheduler.shutdown.assert_not_called()
        # Shutdown runs when the TestClient context exits.
        fake_scheduler.shutdown.assert_called_once_with(wait=False)
