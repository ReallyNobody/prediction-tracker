"""FastAPI application entrypoint for the RMN Hurricane Dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rmn_dashboard import __version__
from rmn_dashboard.database import get_session
from rmn_dashboard.models import CatLoss

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(
    title="RMN Hurricane Dashboard",
    description=(
        "How hurricane risk is being priced, today, by the people who have "
        "to pay for it — translated for people who don't speak insurance."
    ),
    version=__version__,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    db: Session = Depends(get_session),
) -> HTMLResponse:
    """The main dashboard page — six panel shells, no live data yet."""
    cat_loss_count = db.scalar(select(func.count()).select_from(CatLoss)) or 0

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "build_status": "Scaffold · Week 1",
            "cat_loss_count": cat_loss_count,
        },
    )


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> dict[str, str]:
    """Liveness probe for Render/Railway health checks."""
    return {"status": "ok"}
