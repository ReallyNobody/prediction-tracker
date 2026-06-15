/**
 * Panel 7 — "Modeled losses" loader.
 *
 * Reads /api/v1/cat-losses/recent and renders one event with its
 * modeler estimates. The server's "framing" string (which differs by
 * mode — "Modelers are publishing estimates for this event" vs. "Most
 * recent event with modeled losses") goes in the panel header so
 * readers know what they're looking at without us duplicating the
 * mode logic on the client.
 *
 * Each event renders:
 *   - event name + year + consensus midpoint hero number
 *   - one horizontal range bar per modeler — low to high with a
 *     midpoint tick. Point estimates (KCC convention) render as a
 *     dot rather than a bar.
 *   - per-modeler issuance date + optional refinement note + source link
 *   - dispersion across modelers as a small annotation
 *
 * Inline SVG only — no chart-library dependency. Same pattern as the
 * Signal Tape sparklines and the count-curve panel. The bars all share
 * a single $-axis derived from the largest 'high' value across the
 * latest-per-modeler set, so visual comparison across firms is exact.
 */

(function () {
  "use strict";

  // Bar geometry — kept here so the SVG renderer below stays parameter-
  // light. All sizes in CSS pixels at the panel's natural width; the
  // viewBox lets the browser scale.
  const ROW_HEIGHT = 36;
  const BAR_HEIGHT = 12;
  const ROW_LABEL_WIDTH = 220; // modeler name column on the left
  const ROW_NUMERIC_WIDTH = 110; // numeric label column on the right
  const AXIS_PADDING_PCT = 0.1; // 10% headroom on the right of the bar area

  document.addEventListener("DOMContentLoaded", function () {
    const readoutEl = document.getElementById("cat-losses-readout");
    const emptyEl = document.getElementById("cat-losses-empty");
    const framingEl = document.getElementById("cat-losses-framing");

    if (!readoutEl || !emptyEl) {
      return;
    }

    fetch("/api/v1/cat-losses/recent", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("cat-losses-feed " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        const event = payload && payload.event;
        if (!event) {
          showEmpty(
            emptyEl,
            readoutEl,
            "No modeled-loss estimates available yet.",
          );
          return;
        }
        if (framingEl && payload.framing) {
          framingEl.textContent = payload.framing;
        }
        renderReadout(readoutEl, emptyEl, event);
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("cat-losses fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Modeled-losses feed unavailable — try refreshing.</p>';
        readoutEl.classList.add("hidden");
      });
  });

  // --- Rendering ---------------------------------------------------------

  function renderReadout(readoutEl, emptyEl, event) {
    const estimates = event.estimates || [];
    const html =
      buildHeroBlock(event) +
      (estimates.length > 0
        ? buildModelerRows(estimates)
        : '<p class="mt-3 text-xs text-slate-400">No modeler estimates yet.</p>') +
      buildDispersionFooter(event);
    readoutEl.innerHTML = html;
    readoutEl.classList.remove("hidden");
    emptyEl.classList.add("hidden");
  }

  function buildHeroBlock(event) {
    const consensus = formatUsdBillions(event.consensus_midpoint_usd_billions);
    const modelerCount = event.modeler_count || 0;
    const countLabel =
      modelerCount === 1 ? "1 modeler" : modelerCount + " modelers";
    return (
      '<div class="flex items-baseline justify-between gap-4 flex-wrap">' +
      '<div>' +
      '<div class="text-base font-semibold text-slate-900">' +
      escapeHtml(event.event_name) +
      '</div>' +
      '<div class="text-[11px] text-slate-500 mt-0.5">' +
      escapeHtml(String(event.year)) +
      '</div>' +
      '</div>' +
      '<div class="text-right">' +
      '<div class="text-2xl font-mono font-semibold text-slate-900">' +
      consensus +
      '</div>' +
      '<div class="text-[11px] text-slate-500 mt-0.5">' +
      "consensus midpoint · " +
      escapeHtml(countLabel) +
      '</div>' +
      '</div>' +
      '</div>'
    );
  }

  function buildModelerRows(estimates) {
    // Shared $-axis across all rows: scale to the maximum 'high' value
    // in the set, with headroom. Anchored at 0 so the visual reads as
    // "where does this firm's range fall on the absolute scale" rather
    // than relative-to-each-other.
    let scaleMax = 0;
    for (const e of estimates) {
      if (e.high_usd_billions > scaleMax) {
        scaleMax = e.high_usd_billions;
      }
    }
    if (scaleMax <= 0) {
      scaleMax = 1; // degenerate fallback
    }
    scaleMax = scaleMax * (1 + AXIS_PADDING_PCT);

    const rowsSvgHeight = estimates.length * ROW_HEIGHT;
    const innerHTML = estimates
      .map(function (e, i) {
        return renderRow(e, i, scaleMax);
      })
      .join("");

    // Render in a single SVG so all rows share the same coordinate
    // system — the bars line up on a common $-axis without any extra
    // alignment plumbing in the DOM.
    return (
      '<div class="mt-4">' +
      '<svg viewBox="0 0 1000 ' +
      rowsSvgHeight +
      '" ' +
      'preserveAspectRatio="xMidYMid meet" ' +
      'class="w-full" role="img" ' +
      'aria-label="Modeler insured-loss ranges">' +
      buildAxisGrid(scaleMax, rowsSvgHeight) +
      innerHTML +
      "</svg>" +
      "</div>"
    );
  }

  function renderRow(est, index, scaleMax) {
    const y = index * ROW_HEIGHT;
    const barAreaX = ROW_LABEL_WIDTH;
    const barAreaWidth = 1000 - ROW_LABEL_WIDTH - ROW_NUMERIC_WIDTH;
    const lowX = barAreaX + (est.low_usd_billions / scaleMax) * barAreaWidth;
    const highX = barAreaX + (est.high_usd_billions / scaleMax) * barAreaWidth;
    const midX = barAreaX + (est.midpoint_usd_billions / scaleMax) * barAreaWidth;
    const barCenterY = y + ROW_HEIGHT / 2;
    const barTopY = barCenterY - BAR_HEIGHT / 2;

    const modelerLabel =
      '<text x="0" y="' +
      (barCenterY + 4) +
      '" font-size="13" fill="#334155" font-family="ui-sans-serif, system-ui">' +
      escapeHtml(est.modeler) +
      "</text>";

    let barOrDot;
    if (est.is_point_estimate) {
      // Point estimate — render as a dot rather than a degenerate zero-
      // width bar so the reader sees the distinction at a glance.
      barOrDot =
        '<circle cx="' +
        midX +
        '" cy="' +
        barCenterY +
        '" r="6" fill="#3b82f6" stroke="#1e40af" stroke-width="1.5" />';
    } else {
      const width = Math.max(2, highX - lowX); // 2px floor for thin ranges
      barOrDot =
        '<rect x="' +
        lowX +
        '" y="' +
        barTopY +
        '" width="' +
        width +
        '" height="' +
        BAR_HEIGHT +
        '" rx="2" ry="2" fill="#bfdbfe" stroke="#3b82f6" stroke-width="1" />' +
        // Midpoint tick — darker line at the midpoint of the range
        '<line x1="' +
        midX +
        '" y1="' +
        (barTopY - 2) +
        '" x2="' +
        midX +
        '" y2="' +
        (barTopY + BAR_HEIGHT + 2) +
        '" stroke="#1e40af" stroke-width="2" />';
    }

    const numericLabel = est.is_point_estimate
      ? formatUsdBillions(est.midpoint_usd_billions) + " (point)"
      : formatRange(est.low_usd_billions, est.high_usd_billions);
    const numericText =
      '<text x="' +
      (1000 - ROW_NUMERIC_WIDTH + 4) +
      '" y="' +
      (barCenterY + 4) +
      '" font-size="12" fill="#475569" font-family="ui-monospace, SFMono-Regular" >' +
      escapeHtml(numericLabel) +
      "</text>";

    const issuanceLabel =
      '<text x="0" y="' +
      (y + ROW_HEIGHT - 4) +
      '" font-size="10" fill="#94a3b8" font-family="ui-sans-serif, system-ui">' +
      "issued " +
      escapeHtml(est.issued_at) +
      (est.refinement_note
        ? " · " + escapeHtml(truncate(est.refinement_note, 70))
        : "") +
      "</text>";

    // Optional source link rendered as a foreignObject — keeps the
    // anchor clickable without leaving SVG-land. Falls through silently
    // when the source_url is absent.
    let sourceLink = "";
    if (est.source_url) {
      sourceLink =
        '<foreignObject x="' +
        (1000 - ROW_NUMERIC_WIDTH + 4) +
        '" y="' +
        (y + ROW_HEIGHT - 16) +
        '" width="' +
        (ROW_NUMERIC_WIDTH - 8) +
        '" height="14">' +
        '<a href="' +
        escapeHtml(est.source_url) +
        '" target="_blank" rel="noopener" ' +
        'xmlns="http://www.w3.org/1999/xhtml" ' +
        'style="font-size:10px;color:#3b82f6;text-decoration:underline;font-family:ui-sans-serif,system-ui;">' +
        "source" +
        "</a>" +
        "</foreignObject>";
    }

    return (
      '<g data-row="' +
      index +
      '" data-modeler="' +
      escapeHtml(est.modeler) +
      '">' +
      modelerLabel +
      barOrDot +
      numericText +
      issuanceLabel +
      sourceLink +
      "</g>"
    );
  }

  function buildAxisGrid(scaleMax, height) {
    // Sparse vertical reference lines at human-readable round numbers.
    // We pick three or four tick values inside [0, scaleMax] so the
    // bars sit against intuitive landmarks ($10B, $25B, $50B) without
    // crowding.
    const candidates = [10, 25, 50, 100, 200];
    const ticks = candidates.filter(function (v) {
      return v > 0 && v < scaleMax;
    });
    const barAreaX = ROW_LABEL_WIDTH;
    const barAreaWidth = 1000 - ROW_LABEL_WIDTH - ROW_NUMERIC_WIDTH;
    const lines = ticks
      .map(function (v) {
        const x = barAreaX + (v / scaleMax) * barAreaWidth;
        return (
          '<line x1="' +
          x +
          '" y1="0" x2="' +
          x +
          '" y2="' +
          height +
          '" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="2 3" />' +
          '<text x="' +
          (x + 3) +
          '" y="10" font-size="9" fill="#94a3b8" font-family="ui-monospace, SFMono-Regular">$' +
          v +
          "B</text>"
        );
      })
      .join("");
    return lines;
  }

  function buildDispersionFooter(event) {
    const dispersion = event.dispersion_usd_billions || 0;
    if (event.modeler_count < 2 || dispersion <= 0) {
      return "";
    }
    return (
      '<p class="mt-3 text-xs text-slate-500">' +
      "Dispersion across modelers: " +
      formatUsdBillions(dispersion) +
      ". " +
      '<span class="text-slate-400">Wide ranges across firms reflect genuine ' +
      "uncertainty, not error.</span>" +
      "</p>"
    );
  }

  // --- Helpers -----------------------------------------------------------

  function formatUsdBillions(value) {
    if (value == null || isNaN(value)) {
      return "—";
    }
    // Compact: 1 decimal under $100B, integer at or above.
    if (value >= 100) {
      return "$" + Math.round(value) + "B";
    }
    return "$" + Number(value).toFixed(1) + "B";
  }

  function formatRange(low, high) {
    // "$9-13B" — shared 'B' suffix, single hyphen separator.
    const lowStr = low >= 100 ? Math.round(low) : Number(low).toFixed(1);
    const highStr = high >= 100 ? Math.round(high) : Number(high).toFixed(1);
    return "$" + lowStr + "-" + highStr + "B";
  }

  function showEmpty(emptyEl, readoutEl, message) {
    emptyEl.innerHTML = '<p class="text-sm">' + escapeHtml(message) + "</p>";
    emptyEl.classList.remove("hidden");
    readoutEl.classList.add("hidden");
  }

  function truncate(str, max) {
    if (str == null) return "";
    if (str.length <= max) return str;
    return str.slice(0, max - 1) + "…";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
