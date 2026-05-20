/**
 * Panel 4 count-curve loader (Day 46).
 *
 * Reads /api/v1/markets/count-curve and renders the Kalshi hurricane-
 * count threshold ladder as a cumulative probability curve:
 *
 *   X-axis: threshold N (count of hurricanes)
 *   Y-axis: market-implied P(total > N)
 *
 * Each Kalshi contract at threshold N has a yes_price that reads as the
 * market's probability. Plotted left-to-right by ascending N, the points
 * form a descending curve that visualizes the market's consensus
 * distribution at a glance.
 *
 * Editorial design notes:
 *
 *   - Raw points only. No isotonic smoothing. Off-season monotonicity
 *     violations are real signal about thin liquidity in mid-tail
 *     contracts; smoothing them away would lie about what the market
 *     actually shows.
 *   - Median is the labeled hero. The X-coordinate where the curve
 *     crosses 50% is "what the market thinks the season will be."
 *     Vertical dashed line + label.
 *   - Climate-average reference (1991-2020 NOAA = 7.2 hurricanes) sits
 *     as a faded vertical line so readers can see "is the market more
 *     or less bullish than the long-run baseline." Single most useful
 *     comparison point.
 *
 * Inline SVG only — no chart-library dependency. Same pattern as the
 * Signal Tape sparklines. Re-fetch on page reload; refresh cadence is
 * tied to the ingest cycle (Kalshi every 15 min).
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const curveEl = document.getElementById("count-curve");
    if (!curveEl) {
      return;
    }

    fetch("/api/v1/markets/count-curve", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("count-curve " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        renderCurve(curveEl, payload || {});
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("count-curve fetch failed", err);
        renderEmpty(curveEl, "Count-curve feed unavailable.");
      });
  });

  // --- Rendering --------------------------------------------------------

  // Render dimensions. 100% width via responsive SVG viewBox so the
  // curve fills the Panel 4 column on every viewport. Height fixed to
  // keep the aspect predictable in the page layout.
  const W = 560;
  const H = 220;
  const PAD_LEFT = 38;     // y-axis label space
  const PAD_RIGHT = 18;
  const PAD_TOP = 14;
  const PAD_BOTTOM = 28;   // x-axis label space

  // X-axis range: 0 to 20 hurricanes. Wide enough to fit the long
  // Atlantic-season tail; narrow enough that points don't bunch up.
  const X_MIN = 0;
  const X_MAX = 20;

  // Brand palette references — keep colors aligned with the rest of
  // the dashboard's cream surface treatment.
  const COLOR_CURVE = "#1C75BC";      // RMN brand blue (line + points)
  const COLOR_CURVE_FILL = "#1C75BC"; // same; opacity in attribute
  const COLOR_MEDIAN = "#C47B5F";     // terracotta — divider color
  const COLOR_CLIMATE = "#888780";    // warm slate — neutral reference
  const COLOR_AXIS = "#B4B2A9";
  const COLOR_GRID = "#E8E2D8";
  const COLOR_TEXT = "#5F5E5A";
  const COLOR_TEXT_STRONG = "#2C2B27";

  function renderCurve(curveEl, payload) {
    const points = Array.isArray(payload.points) ? payload.points : [];
    if (points.length < 2) {
      renderEmpty(
        curveEl,
        "Not enough Kalshi count contracts ingested yet for a meaningful curve."
      );
      return;
    }

    const seasonLabel = payload.season_label || "";
    const median = typeof payload.median === "number" ? payload.median : null;
    const climateAvg = typeof payload.climate_average === "number"
      ? payload.climate_average
      : null;
    const anomalyCount = Array.isArray(payload.anomalies)
      ? payload.anomalies.length
      : 0;
    const asOf = payload.as_of ? formatAsOf(payload.as_of) : "";

    // Build the SVG content. Use viewBox-based scaling so the curve
    // fills its container width without locking to a fixed pixel size.
    const svg = buildSvg(points, median, climateAvg);

    // Caption — varies by whether we found anomalies. Editorial honesty:
    // call out the monotonicity wobble so readers know what they're
    // seeing isn't a rendering bug.
    const captionSeasonal = "1991-2020 average shown as the slate reference line.";
    const captionAnomaly = anomalyCount > 0
      ? " Some mid-tail points violate the strict P(>N+1) ≤ P(>N) inequality — that's off-season illiquidity, not a rendering glitch."
      : "";
    const captionAsOf = asOf
      ? '<span class="ml-2 font-mono text-slate-400">as of ' + escapeHtml(asOf) + "</span>"
      : "";

    curveEl.innerHTML =
      '<div class="flex items-baseline justify-between mb-2 flex-wrap gap-2">' +
        '<h3 class="text-xs uppercase tracking-wide font-semibold" style="color: #5F5E5A">' +
          escapeHtml(seasonLabel) + ' Atlantic count, market-implied' +
        "</h3>" +
        '<div class="text-[10px] font-mono" style="color: #888780">' +
          (median !== null
            ? 'Market median: <span style="color: ' + COLOR_TEXT_STRONG + '">~' +
              escapeHtml(median.toFixed(1)) + " hurricanes</span>"
            : "") +
        "</div>" +
      "</div>" +
      svg +
      '<p class="mt-2 text-xs leading-relaxed" style="color: #5F5E5A">' +
        'Each point is a Kalshi binary on "more than N Atlantic hurricanes in ' +
          escapeHtml(seasonLabel) +
        '." The market-clearing Yes price reads as probability. ' +
        captionSeasonal + captionAnomaly +
        captionAsOf +
      "</p>";
  }

  function renderEmpty(curveEl, message) {
    curveEl.innerHTML =
      '<p class="text-xs italic" style="color: #888780">' +
        escapeHtml(message) +
      "</p>";
  }

  function buildSvg(points, median, climateAvg) {
    // Plot region inside the padded canvas.
    const innerW = W - PAD_LEFT - PAD_RIGHT;
    const innerH = H - PAD_TOP - PAD_BOTTOM;

    // Coordinate transforms.
    const xOf = function (threshold) {
      const t = (threshold - X_MIN) / (X_MAX - X_MIN);
      return PAD_LEFT + t * innerW;
    };
    const yOf = function (pct) {
      // pct = 0.0–1.0; high values toward top.
      return PAD_TOP + (1 - pct) * innerH;
    };

    // Y gridlines + labels at 0/25/50/75/100%.
    const yGridLines = [0, 0.25, 0.5, 0.75, 1].map(function (p) {
      const y = yOf(p);
      return (
        '<line x1="' + PAD_LEFT + '" y1="' + y.toFixed(1) +
          '" x2="' + (PAD_LEFT + innerW).toFixed(1) + '" y2="' + y.toFixed(1) +
          '" stroke="' + COLOR_GRID + '" stroke-width="0.5" />' +
        '<text x="' + (PAD_LEFT - 6) + '" y="' + (y + 3).toFixed(1) +
          '" text-anchor="end" font-size="10" fill="' + COLOR_TEXT + '">' +
          (p * 100).toFixed(0) + "%" + "</text>"
      );
    }).join("");

    // X-axis ticks at 0, 5, 10, 15, 20.
    const xTicks = [0, 5, 10, 15, 20].map(function (n) {
      const x = xOf(n);
      return (
        '<line x1="' + x.toFixed(1) + '" y1="' + (PAD_TOP + innerH).toFixed(1) +
          '" x2="' + x.toFixed(1) + '" y2="' + (PAD_TOP + innerH + 4).toFixed(1) +
          '" stroke="' + COLOR_AXIS + '" stroke-width="0.6" />' +
        '<text x="' + x.toFixed(1) + '" y="' + (PAD_TOP + innerH + 17).toFixed(1) +
          '" text-anchor="middle" font-size="10" fill="' + COLOR_TEXT + '">' +
          n + "</text>"
      );
    }).join("");

    // X-axis label.
    const xAxisLabel =
      '<text x="' + (PAD_LEFT + innerW / 2).toFixed(1) +
        '" y="' + (H - 4).toFixed(1) +
        '" text-anchor="middle" font-size="10" font-style="italic" fill="' + COLOR_TEXT + '">' +
        'more than N hurricanes' + "</text>";

    // Curve path. Connect points in order; clip to plot region.
    const linePoints = points.map(function (p) {
      const x = xOf(p.threshold);
      const y = yOf(p.yes_price);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");

    // Soft fill underneath the curve — anchored at bottom-left and
    // bottom-right of the line.
    const firstPt = points[0];
    const lastPt = points[points.length - 1];
    const fillPath = (
      'M ' + xOf(firstPt.threshold).toFixed(1) + " " +
            yOf(firstPt.yes_price).toFixed(1) + " " +
      'L ' + linePoints.split(" ").slice(1).map(function (xy) {
        return xy.replace(",", " ");
      }).join(" L ") + " " +
      'L ' + xOf(lastPt.threshold).toFixed(1) + " " + (PAD_TOP + innerH).toFixed(1) + " " +
      'L ' + xOf(firstPt.threshold).toFixed(1) + " " + (PAD_TOP + innerH).toFixed(1) + " Z"
    );

    // Point markers + hover titles.
    const markers = points.map(function (p) {
      const x = xOf(p.threshold);
      const y = yOf(p.yes_price);
      const tip = "More than " + p.threshold + ": " +
                  (p.yes_price * 100).toFixed(0) + "%";
      return (
        '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) +
          '" r="3.5" fill="' + COLOR_CURVE + '" stroke="#FFFFFF" stroke-width="1">' +
          '<title>' + escapeHtml(tip) + "</title>" +
        "</circle>"
      );
    }).join("");

    // Median marker — vertical dashed line + label at top.
    let medianMarker = "";
    if (typeof median === "number" && median >= X_MIN && median <= X_MAX) {
      const x = xOf(median);
      medianMarker =
        '<line x1="' + x.toFixed(1) + '" y1="' + PAD_TOP +
          '" x2="' + x.toFixed(1) + '" y2="' + (PAD_TOP + innerH).toFixed(1) +
          '" stroke="' + COLOR_MEDIAN + '" stroke-width="1.4" ' +
          'stroke-dasharray="4 3" />' +
        '<text x="' + (x + 4).toFixed(1) + '" y="' + (PAD_TOP + 10).toFixed(1) +
          '" font-size="10" font-weight="500" fill="' + COLOR_MEDIAN + '">' +
          'median ~' + median.toFixed(1) + "</text>";
    }

    // Climate-average reference — faded vertical line.
    let climateMarker = "";
    if (typeof climateAvg === "number" && climateAvg >= X_MIN && climateAvg <= X_MAX) {
      const x = xOf(climateAvg);
      climateMarker =
        '<line x1="' + x.toFixed(1) + '" y1="' + PAD_TOP +
          '" x2="' + x.toFixed(1) + '" y2="' + (PAD_TOP + innerH).toFixed(1) +
          '" stroke="' + COLOR_CLIMATE + '" stroke-width="1" ' +
          'stroke-dasharray="2 4" opacity="0.7" />' +
        '<text x="' + (x + 4).toFixed(1) + '" y="' + (PAD_TOP + innerH - 4).toFixed(1) +
          '" font-size="9" fill="' + COLOR_CLIMATE + '">' +
          'climate avg' + "</text>";
    }

    return (
      '<svg viewBox="0 0 ' + W + " " + H + '" ' +
        'preserveAspectRatio="xMidYMid meet" ' +
        'style="width: 100%; height: auto; display: block; max-height: 240px;" ' +
        'role="img" aria-label="Market-implied probability of more than N Atlantic hurricanes">' +
        yGridLines +
        xTicks +
        xAxisLabel +
        // Plot area outline (just the axes, not a full frame).
        '<line x1="' + PAD_LEFT + '" y1="' + PAD_TOP + '" x2="' + PAD_LEFT +
          '" y2="' + (PAD_TOP + innerH).toFixed(1) +
          '" stroke="' + COLOR_AXIS + '" stroke-width="0.8" />' +
        '<line x1="' + PAD_LEFT + '" y1="' + (PAD_TOP + innerH).toFixed(1) +
          '" x2="' + (PAD_LEFT + innerW).toFixed(1) +
          '" y2="' + (PAD_TOP + innerH).toFixed(1) +
          '" stroke="' + COLOR_AXIS + '" stroke-width="0.8" />' +
        // Fill under curve (faint).
        '<path d="' + fillPath + '" fill="' + COLOR_CURVE_FILL +
          '" fill-opacity="0.08" stroke="none" />' +
        // Climate reference (rendered before median so median sits visually on top).
        climateMarker +
        // Median marker.
        medianMarker +
        // Curve line.
        '<polyline points="' + linePoints +
          '" fill="none" stroke="' + COLOR_CURVE + '" stroke-width="1.8" ' +
          'stroke-linecap="round" stroke-linejoin="round" />' +
        // Point markers + tooltips.
        markers +
      "</svg>"
    );
  }

  // --- Helpers ----------------------------------------------------------

  function formatAsOf(iso) {
    // ISO-8601 datetime → "HH:MMZ" for the right-side stamp. Same
    // convention used elsewhere on the dashboard.
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
