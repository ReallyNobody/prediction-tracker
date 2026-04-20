"""FastAPI application entrypoint for the RMN Hurricane Dashboard."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rmn_dashboard import __version__

app = FastAPI(
    title="RMN Hurricane Dashboard",
    description=(
        "How hurricane risk is being priced, today, by the people who have "
        "to pay for it — translated for people who don't speak insurance."
    ),
    version=__version__,
)


@app.get("/", response_class=JSONResponse)
async def root() -> dict[str, str]:
    """Placeholder landing endpoint. Replaced by a Jinja template in Week 1 Day 3."""
    return {
        "message": "Hello, Hurricane Season",
        "version": __version__,
        "status": "scaffold",
    }


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> dict[str, str]:
    """Liveness probe for Render/Railway health checks."""
    return {"status": "ok"}
