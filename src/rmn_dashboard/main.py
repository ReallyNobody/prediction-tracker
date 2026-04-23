"""FastAPI application entrypoint for the RMN Hurricane Dashboard."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rmn_dashboard import __version__
from rmn_dashboard.config import settings
from rmn_dashboard.database import get_session
from rmn_dashboard.models import CatLoss
from rmn_dashboard.scheduler import build_scheduler
from rmn_dashboard.services.markets import latest_hurricane_markets

# Configure logging before any module-level logger calls. Python's default
# root logger level is WARNING, which silently drops every ``logger.info(...)``
# call in our code — including the scheduler's "persisted N rows" confirmation.
# uvicorn and gunicorn configure their own named loggers (uvicorn.access,
# uvicorn.error, gunicorn.error) explicitly, so this root-level basicConfig
# doesn't conflict with theirs. pytest's ``caplog`` fixture attaches its
# handler at test time (after this import-time call), so test capture still
# works.
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the background scheduler on app startup and stop it on shutdown.

    Gated by ``settings.scheduler_enabled`` — off in dev (so uvicorn --reload
    doesn't double-fire every save) and in the test suite, on in prod via
    env var. When disabled the app runs exactly as before.
    """
    # TEMP diagnostic — confirm how env vars flow through pydantic on Render.
    # Remove once the scheduler is verified firing in prod.
    logger.info(
        "Lifespan boot: scheduler_enabled=%r interval=%r env=%r",
        settings.scheduler_enabled,
        settings.kalshi_ingest_interval_minutes,
        settings.env,
    )
    scheduler = None
    if settings.scheduler_enabled:
        scheduler = build_scheduler(settings.kalshi_ingest_interval_minutes)
        scheduler.start()
        logger.info(
            "Scheduler started; Kalshi ingest every %d minutes",
            settings.kalshi_ingest_interval_minutes,
        )
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")


app = FastAPI(
    title="RMN Hurricane Dashboard",
    description=(
        "How hurricane risk is being priced, today, by the people who have "
        "to pay for it — translated for people who don't speak insurance."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    db: Session = Depends(get_session),
) -> HTMLResponse:
    """The main dashboard page — panel shells plus the live Kalshi markets list."""
    cat_loss_count = db.scalar(select(func.count()).select_from(CatLoss)) or 0
    markets = latest_hurricane_markets(db)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "build_status": "Scaffold · Week 1",
            "cat_loss_count": cat_loss_count,
            "markets": markets,
        },
    )


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> dict[str, str]:
    """Liveness probe for Render/Railway health checks."""
    return {"status": "ok"}
