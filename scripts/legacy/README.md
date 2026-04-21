# Legacy Scripts

These are the original prototype scripts that seeded the project. They are preserved for reference only — the active codebase is in `src/rmn_dashboard/`.

## Port status

Each file is annotated with its fate in the new architecture.

### High value — porting with refactor

- **`kalshi_authenticated_scraper.py`** — RSA-PSS authenticated Kalshi API client. Becomes `src/rmn_dashboard/scrapers/kalshi.py`. The signing logic is correct; we only change path handling, async HTTP, and config loading.
- **`sec_cat_loss_scraper.py`** — SEC EDGAR 10-Q/10-K catastrophe loss scraper using BeautifulSoup + regex keyword extraction. Becomes `src/rmn_dashboard/scrapers/sec.py`. Keep extraction logic; clean up hardcoded `/home/claude/` paths and move to async httpx.

### Medium value — porting selectively

- **`prediction_market_scraper_v2.py`** — Kalshi + PredictIt + Polymarket public API scraper with improved keyword filters. Port only the Polymarket Gamma API portion to `src/rmn_dashboard/scrapers/polymarket.py`. Kalshi is superseded by the authenticated scraper; PredictIt is out of scope for the hurricane dashboard.
- **`cat_loss_database.py`** — Raw `sqlite3` database wrapper. Schema concepts (company, ticker, event_name, losses, context) carry into `src/rmn_dashboard/models/cat_loss.py` as a SQLAlchemy model. The file itself is not ported.

### Low value — archived, not ported

- **`prediction_market_scraper.py`** (v1) — Superseded by v2.
- **`kalshi_weather_scraper_v3.py`** / **`kalshi_weather_scraper_v4.py`** — Pre-authentication versions; superseded by `kalshi_authenticated_scraper.py`.
- **`prediction_market_analyzer.py`** — Matplotlib chart generator using static sample data. Chart patterns are useful design references, but we use Plotly in the new app, not matplotlib.
- **`newsletter_charts.py`** / **`kalshi_newsletter_charts.py`** — One-off PNG chart generators for Substack embedding. Out of scope for the dashboard.
- **`cat_loss_dashboard.html`** / **`kalshi_dashboard.html`** — Static single-file HTML prototypes. Jinja2 templates in `src/rmn_dashboard/templates/` replace these properly.

## Why keep them

Reference value. When porting any piece of logic — especially the Kalshi signing — it helps to have the original working code at hand. These files are not imported or executed by the new app, and they will not be updated going forward.

Do not add new functionality to any file in this directory. If you find yourself wanting to, add it to the new `src/rmn_dashboard/` package instead.
