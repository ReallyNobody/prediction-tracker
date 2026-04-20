# RMN Hurricane Dashboard

*A Risk Market News project. Launch target: June 1, 2026 (Atlantic hurricane season opener).*

**The thesis.** How hurricane risk is being priced, today, by the people who have to pay for it — translated for people who don't speak insurance. This is a public, server-rendered web dashboard for a non-insurance professional audience: institutional investors, journalists, policy analysts, emergency managers. Six panels: active storms and outlook, carrier exposure, cat bond spreads, a small honest prediction-market sidebar, historical loss analogs, and a "what changed today" summary.

## Status

Early scaffold. Week 1 of 6 in the build plan. See `/docs/` for the architecture document and supporting memos.

## Stack

- **FastAPI** (async Python) with server-rendered **Jinja2** templates and **HTMX** partials — no JS build step
- **Plotly.js** for charts, loaded from CDN
- **Tailwind CSS** via CDN for MVP styling
- **SQLAlchemy 2.x** + **Alembic** over **SQLite** in dev, **Postgres** in production
- **APScheduler** for periodic data ingestion
- **httpx** async client for all outbound scraping
- Hosted on **Render** (app service + managed Postgres, ~$14/month)

## Repo layout

```
prediction-tracker/
├── src/rmn_dashboard/        # The app (FastAPI package)
│   ├── main.py               # FastAPI entrypoint
│   ├── config.py             # pydantic-settings
│   ├── models/               # SQLAlchemy models
│   ├── scrapers/             # Kalshi, Polymarket, SEC, NHC, Plenum, NAIC
│   ├── routes/               # pages + HTMX partials
│   ├── services/             # translation layer
│   ├── tasks/                # APScheduler jobs
│   ├── templates/            # Jinja2 HTML
│   ├── static/               # CSS/JS
│   └── utils/                # helpers
├── scripts/legacy/           # Prototype scripts preserved for reference only
├── docs/                     # Architecture, memos, planning docs
├── tests/                    # pytest suite
├── alembic/                  # Database migrations (added Day 2)
├── data/                     # Local SQLite dev DB (git-ignored)
├── pyproject.toml
├── .env.example
└── README.md
```

## Local development

Requires Python 3.11+.

```bash
# Create a venv and install the app in editable mode with dev extras
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# Copy the example env file and edit as needed
cp .env.example .env

# Run the dev server
uvicorn rmn_dashboard.main:app --reload

# Open http://127.0.0.1:8000
```

At this stage the root route returns a JSON stub. Full panel layout lands in Week 1 Day 3.

## Tests and linting

```bash
pytest
ruff check src tests
mypy src
```

## Data sources

| Panel | Source | Access |
|---|---|---|
| Active storms | NHC / NOAA public feeds, HURDAT2 | Public |
| Carrier exposure | NAIC statutory filings, SEC 10-Q/10-K | Public |
| Cat bond spreads | Plenum UCITS Cat Bond Fund Index | Public, weekly |
| Prediction markets | Kalshi (authenticated API), Polymarket Gamma | Keyed / Public |
| Historical analogs | Preserved SEC-derived cat loss dataset | Vendored |

**Artemis.bm is not used** as a data source. Their terms prohibit commercial and AI use of their data without a paid license; see `docs/cat_bond_data_sources_memo.md` for the full rationale and alternatives.

## Build plan

Week 1: scaffold (this week). Week 2: data ingestion. Week 3: forecast + exposure panels. Week 4: market pricing panels. Week 5: translation layer + polish. Week 6: launch prep + June 1 go-live. See `docs/prediction_tracker_architecture.md`.

## License

Proprietary. Risk Market News.
