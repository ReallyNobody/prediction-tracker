/**
 * Signal Tape — page-top hurricane risk anchor (Day 43).
 *
 * Reads /api/v1/signal-tape and renders a four-cell horizontal band:
 * Storms · Equities · Risk capital · Markets. Each cell carries a
 * current tier (quiet / watching / active / severe), a value word, a
 * one-line driver, and a 14-day daily-aggregate sparkline.
 *
 * The composite "tone today" word at the left is the max-tier across
 * the four cells, derived server-side. Cells are color-coded by tier;
 * direction is otherwise carried by the cell content itself.
 *
 * Sparklines are inline SVG, no charting-library dependency. Each
 * sparkline is a polyline + a soft fill underneath, scaled to its own
 * value range so a flat market and a spinning-up basin both read with
 * useful resolution.
 *
 * Renders honestly when data depth is shallow — a sparkline with 2
 * points is a sparkline with 2 points. An empty history array drops
 * the sparkline entirely rather than fabricating a flat line.
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const tapeEl = document.getElementById("signal-tape");
    if (!tapeEl) {
      return;
    }

    fetch("/api/v1/signal-tape", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("signal-tape " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        renderTape(tapeEl, payload || {});
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("signal-tape fetch failed", err);
        renderError(tapeEl);
      });
  });

  // --- Tier styling -----------------------------------------------------

  // Tier → color hex. Single source of truth for the cell tinting +
  // sparkline stroke. Day 44 re-tone for the cream brand palette —
  // values match the --rmn-tier-* CSS variables in base.html. Mirroring
  // them in JS (rather than reading the computed CSS values) keeps the
  // SVG sparkline rendering stable across browsers + theme contexts.
  const TIER_COLOR = {
    quiet: "#888780",     // warm slate
    watching: "#C47B5F",  // terracotta — matches --rmn-divider
    active: "#A0533D",    // deeper rust
    severe: "#7A2E1E",    // deep brick
  };
  const TIER_FALLBACK_COLOR = TIER_COLOR.quiet;

  function tierColor(tier) {
    return TIER_COLOR[tier] || TIER_FALLBACK_COLOR;
  }

  // --- Rendering --------------------------------------------------------

  function renderTape(tapeEl, payload) {
    const cells = Array.isArray(payload.cells) ? payload.cells : [];
    const tone = payload.tone || "quiet";
    const toneLabel = payload.tone_label || "Quiet";
    const asOf = payload.as_of ? formatAsOf(payload.as_of) : "";

    const toneColor = tierColor(tone);

    // Day 44: relabel "Tone today" → "Risk today · 14-day context".
    // The word that follows (Watching / Active / Severe) is a current-
    // state tier, not a trend direction — the 14-day sparklines below
    // are the trend element. The label honestly carries both: "Risk
    // today" for the word, "14-day context" for the cells underneath.
    //
    // Tone word renders in Source Serif 4 to echo the page-title
    // heading family, giving the band a typographic anchor that pairs
    // with the cream surface and terracotta accents.
    tapeEl.innerHTML =
      '<div class="flex items-center justify-between mb-3 flex-wrap gap-2">' +
        '<div class="flex items-baseline gap-3 flex-wrap">' +
          '<div class="text-[10px] uppercase tracking-wider" ' +
               'style="color: #888780;">Risk today</div>' +
          '<div class="text-xl font-medium" ' +
               'style="color: ' + toneColor + '; ' +
                      'font-family: \'Source Serif 4\', Georgia, serif; ' +
                      'line-height: 1;">' +
            escapeHtml(toneLabel) +
          "</div>" +
          '<div class="text-[10px] italic" style="color: #888780;">' +
            '· 14-day context' +
          "</div>" +
        "</div>" +
        '<div class="text-[10px] font-mono" style="color: #888780;">' +
          (asOf ? "as of " + escapeHtml(asOf) : "") +
        "</div>" +
      "</div>" +
      '<div class="grid grid-cols-2 lg:grid-cols-4 gap-2.5">' +
        cells.map(buildCell).join("") +
      "</div>";
  }

  function renderError(tapeEl) {
    // Editorial: silent on error rather than scary red banner. The
    // dashboard's other panels still work; the anchor just sits empty.
    tapeEl.innerHTML =
      '<div class="text-xs text-slate-400">Signal tape unavailable — refresh to retry.</div>';
  }

  function buildCell(cell) {
    const color = tierColor(cell.tier);
    // Day 44a: bumped value text contrast against the cream surface.
    // The original #5F5E5A read washed-out at the 15px size; #2C2B27
    // (near-black warm) is the floor for body-weight readability on
    // a #F7F4F1 background. Non-quiet cells still colorize the value
    // word to the tier color for visual cohesion.
    const valueColor = cell.tier === "quiet" ? "#2C2B27" : color;
    const sparkline = (cell.history && cell.history.length > 0)
      ? buildSparkline(cell.history, color)
      : "";

    return (
      '<div ' +
        'style="background: #FFFFFF; ' +
              'border: 1px solid #E8E2D8; ' +
              'border-left: 3px solid ' + color + '; ' +
              'border-radius: 4px; padding: 10px 12px;">' +
        '<div class="text-[10px] uppercase tracking-wider mb-1" ' +
             'style="color: #5F5E5A;">' +
          escapeHtml(cell.label || "") +
        "</div>" +
        '<div class="flex items-center justify-between gap-2">' +
          '<div class="min-w-0">' +
            '<div class="text-[15px] font-medium leading-tight" ' +
                 'style="color: ' + valueColor + '">' +
              escapeHtml(cell.value || "—") +
            "</div>" +
            '<div class="text-[11px] font-mono mt-0.5 truncate" ' +
                 'style="color: #5F5E5A;" ' +
                 'title="' + escapeHtml(cell.driver || "") + '">' +
              escapeHtml(cell.driver || "") +
            "</div>" +
          "</div>" +
          sparkline +
        "</div>" +
      "</div>"
    );
  }

  // --- Sparkline --------------------------------------------------------

  // Render dimensions. Width fits the cell's right-side gap (the value +
  // driver text columns flex to fill the rest); height matches the
  // value+driver text stack so the cell stays visually balanced.
  const SPARK_W = 80;
  const SPARK_H = 34;
  const SPARK_PAD = 2;  // pixels of vertical padding inside viewBox

  function buildSparkline(history, color) {
    // Each history point is {date, value}. We project value to the y
    // axis (with vertical padding) and date order to the x axis
    // (evenly spaced; we don't try to honor calendar gaps because the
    // ingest cadence is regular enough that gaps would be data drops,
    // not actual quiet days).
    const values = history.map(function (p) { return Number(p.value) || 0; });
    if (values.length === 0) return "";

    const lo = Math.min.apply(null, values);
    const hi = Math.max.apply(null, values);
    const range = hi - lo;
    // Defensive: a flat series (all same value) projects to a single
    // y-coordinate; render as a centered horizontal line.
    const yOf = function (v) {
      if (range === 0) return SPARK_H / 2;
      // Invert: high values → top of viewBox.
      return SPARK_PAD + (1 - (v - lo) / range) * (SPARK_H - 2 * SPARK_PAD);
    };

    const n = values.length;
    const xOf = function (i) {
      if (n === 1) return SPARK_W / 2;
      return (i / (n - 1)) * SPARK_W;
    };

    // Line points.
    const linePoints = values
      .map(function (v, i) { return xOf(i).toFixed(1) + "," + yOf(v).toFixed(1); })
      .join(" ");
    // Fill polygon — same points + the two bottom corners.
    const fillPoints = linePoints +
                       " " + SPARK_W + "," + SPARK_H +
                       " 0," + SPARK_H;

    return (
      '<svg width="' + SPARK_W + '" height="' + SPARK_H + '" ' +
        'viewBox="0 0 ' + SPARK_W + ' ' + SPARK_H + '" ' +
        'style="flex-shrink: 0">' +
        '<polyline points="' + fillPoints + '" ' +
          'fill="' + color + '" fill-opacity="0.1" stroke="none" />' +
        '<polyline points="' + linePoints + '" ' +
          'fill="none" stroke="' + color + '" stroke-width="1.4" ' +
          'stroke-linecap="round" stroke-linejoin="round" />' +
      "</svg>"
    );
  }

  // --- Helpers ----------------------------------------------------------

  function formatAsOf(iso) {
    // ISO-8601 datetime → "HH:MMZ" for the right-side stamp. Same
    // convention used by Panel 6's changes-as-of element.
    if (typeof iso !== "string" || iso.length < 16) return "";
    return iso.slice(11, 16) + "Z";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
