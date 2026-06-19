/**
 * Prediction Markets — US Landfall Odds hero cards.
 *
 * Renders three large editorial cards for the highest-volume binary
 * outcome markets on the 2026 Atlantic season:
 *
 *   1. Polymarket: any Cat 4 US landfall before 2027
 *   2. Polymarket: any Cat 5 US landfall before 2027
 *   3. Kalshi: more than 2 major (Cat 3+) Atlantic hurricanes in 2026
 *
 * Each card shows the headline Yes price (cents), the 24h delta with
 * direction indicator, and a small caption identifying the market.
 *
 * Data: reuses /api/v1/heat-map/prediction-markets — every cell we
 * need is already in that response (yes_price + delta_24h are the
 * payload's primary fields). No new endpoint required.
 *
 * Editorial decisions:
 *   - Three cards, not two: keeps the row visually balanced and gives
 *     a mix of binary landfall outcomes (Cat 4, Cat 5) plus a count
 *     threshold (major-hurricanes ≥2) representing the central
 *     "active season" expectation.
 *   - Red for "Yes price rose" (probability went UP) and blue for
 *     "Yes price fell" — same diverging palette as the heat map so the
 *     editorial signal is consistent across the panel.
 *   - "no Δ yet" caption for first-day cells where we haven't paired
 *     two snapshots ≥23h apart yet.
 */

(function () {
  "use strict";

  // The three canonical questions to surface as hero cards. Order
  // matters — left-to-right on desktop. Cat 4 first (highest volume),
  // Cat 5 second (the dramatic extreme), majors-≥2 third (the broader
  // "is this an active season at all" central tendency).
  const CARDS = [
    {
      question_id: "us-cat4-landfall-2026",
      platform: "polymarket",
      title: "Cat 4 US landfall",
      subtitle: "Any landfall before 2027 (Polymarket)",
    },
    {
      question_id: "us-cat5-landfall-2026",
      platform: "polymarket",
      title: "Cat 5 US landfall",
      subtitle: "Any landfall before 2027 (Polymarket)",
    },
    {
      question_id: "atlantic-major-ge-2",
      platform: "kalshi",
      title: "Majors ≥2",
      subtitle: "Cat 3+ count for 2026 season (Kalshi)",
    },
  ];

  document.addEventListener("DOMContentLoaded", function () {
    const container = document.getElementById("landfall-odds-cards");
    if (!container) {
      return;
    }

    fetch("/api/v1/heat-map/prediction-markets", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("landfall-odds " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        render(container, payload);
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("landfall-odds fetch failed", err);
        container.innerHTML =
          '<p class="text-xs text-rose-500 col-span-full">Failed to load landfall odds — try refreshing.</p>';
      });
  });

  function render(container, payload) {
    // Index the heat-map cells by (platform, question_id) for O(1)
    // lookup. Cells we don't surface here just stay in the heat map
    // above; no need to filter or dedup.
    const cellMap = new Map();
    for (const c of payload.cells || []) {
      cellMap.set(c.platform + "|" + c.question_id, c);
    }

    const html = CARDS.map(function (card) {
      const cell = cellMap.get(card.platform + "|" + card.question_id);
      return renderCard(card, cell);
    }).join("");

    container.innerHTML = html;
  }

  function renderCard(card, cell) {
    const hasData = cell && cell.has_data;
    const yesPrice = hasData ? formatCents(cell.yes_price) : "—";

    let deltaHtml;
    if (!hasData) {
      deltaHtml = '<p class="mt-1 text-xs text-slate-400">no data</p>';
    } else if (cell.delta_24h == null) {
      deltaHtml = '<p class="mt-1 text-xs text-slate-400 italic">no Δ yet</p>';
    } else {
      const sign = cell.delta_24h > 0 ? "+" : "";
      const arrow =
        cell.delta_24h > 0 ? "▲" : cell.delta_24h < 0 ? "▼" : "—";
      // Diverging palette matching the heat map: red when probability
      // rose, blue when it fell. Editorial signal is consistent.
      let colorClass = "text-slate-600";
      if (cell.delta_24h > 0.5) colorClass = "text-rose-600 font-medium";
      else if (cell.delta_24h < -0.5) colorClass = "text-blue-600 font-medium";
      deltaHtml =
        '<p class="mt-1 text-xs font-mono ' +
        colorClass +
        '">' +
        arrow +
        " " +
        sign +
        Number(cell.delta_24h).toFixed(1) +
        "¢ in 24h</p>";
    }

    return (
      '<div class="rounded-lg border border-slate-200 bg-slate-50/40 p-4">' +
      '<p class="text-xs text-slate-700 font-medium">' +
      escapeHtml(card.title) +
      "</p>" +
      '<p class="text-[10px] text-slate-400 mt-0.5">' +
      escapeHtml(card.subtitle) +
      "</p>" +
      '<p class="mt-3 text-3xl font-mono font-semibold text-slate-900">' +
      yesPrice +
      '<span class="text-base text-slate-400 ml-0.5">¢</span>' +
      "</p>" +
      deltaHtml +
      "</div>"
    );
  }

  function formatCents(price) {
    return Number(price).toFixed(0);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
