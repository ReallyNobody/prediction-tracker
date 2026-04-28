#!/usr/bin/env python3
"""Render og-image.svg to og-image.png at the canonical 1200x630 size.

Day 22 — closes the pre-launch TODO Day 19 left in base.html. Twitter's
summary_large_image card requires a raster image (PNG/JPEG); SVG is not
accepted. Most other platforms (Facebook, LinkedIn, Slack, iMessage,
Discord) render either, but PNG is the universal denominator, so we
ship PNG as the primary `og:image` reference and keep the SVG around as
a secondary asset.

The PNG is a committed artifact, not a build-time output: the SVG only
changes when the brand evolves, and shipping a pre-rendered file means
we don't pay a Cairo dep cost at server start. Re-run this script
whenever ``og-image.svg`` is edited and commit the regenerated PNG.

Usage::

    python scripts/build_og_png.py

Dependencies:

    cairosvg lives in the [dev] extras of pyproject.toml. The build
    script is the only thing in the project that depends on it; the
    runtime app and tests do not. Install via::

        pip install -e '.[dev]'

    cairosvg in turn needs Cairo and Pango (system libraries). On
    macOS these come with Homebrew (``brew install cairo pango``);
    on Debian/Ubuntu, ``apt install libcairo2 libpango-1.0-0``.
    Most dev laptops have them already as part of typical desktop
    installs.

Font rendering note:

    The SVG specifies ``ui-sans-serif, system-ui, -apple-system, ...``
    as its font stack — a CSS keyword stack that browsers resolve to
    the OS UI font. Cairo doesn't honor those keywords; it falls back
    to whatever generic sans-serif is available (typically DejaVu
    Sans on Linux, Arial on macOS). The visual hierarchy (large bold
    headline, light tracked subtitle) is preserved either way, and
    crawlers cache the rendered PNG, so the choice of fallback font
    only matters at build time on whichever machine ran this script.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Repo-root-relative paths. The script can be invoked from any CWD;
# resolve the static dir off __file__ so it works the same from the
# repo root, from scripts/, or from a CI runner.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = _REPO_ROOT / "src" / "rmn_dashboard" / "static"
_SVG_SOURCE = _STATIC_DIR / "og-image.svg"
_PNG_TARGET = _STATIC_DIR / "og-image.png"

# Open Graph canonical card size — same numbers Day 19 used in the SVG
# viewBox and in the og:image:width / og:image:height meta tags.
_OG_WIDTH = 1200
_OG_HEIGHT = 630


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        import cairosvg
    except ImportError:
        logger.error(
            "cairosvg not installed. Install via `pip install -e '.[dev]'` "
            "(it lives in the dev extras only)."
        )
        return 1

    if not _SVG_SOURCE.exists():
        logger.error("source SVG not found at %s", _SVG_SOURCE)
        return 1

    cairosvg.svg2png(
        url=str(_SVG_SOURCE),
        write_to=str(_PNG_TARGET),
        output_width=_OG_WIDTH,
        output_height=_OG_HEIGHT,
    )

    size_kb = _PNG_TARGET.stat().st_size / 1024
    logger.info(
        "Wrote %s (%dx%d, %.1f KB)",
        _PNG_TARGET.relative_to(_REPO_ROOT),
        _OG_WIDTH,
        _OG_HEIGHT,
        size_kb,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
