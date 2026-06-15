/**
 * Panel 8 — "Prediction-market heat-map" loader.
 *
 * Reads /api/v1/heat-map/prediction-markets and renders a 2D grid
 * where rows are prediction-market platforms (Kalshi, Polymarket) and
 * columns are canonical hurricane questions. Each cell shows the
 * current yes-price and the day-over-day delta, colored by direction
 * and magnitude.
 *
 * Color scheme:
 *   - Red = yes-price rose (positive delta — market raised the
 *     probability of the outcome).
 *   - Blue = yes-price fell (negative delta).
 *   - Intensity = |delta| relative to the day's biggest mover.
 *   - Gray = missing cell (either the platform doesn't carry that
 *     question, or there's no recent snapshot).
 *
 * The single shared color scale across all cells means visual
 * comparison across rows is meaningful — "Kalshi moved harder on
 * count-5 than Polymarket did" reads correctly off the panel.
 *
 * Hover tooltip: native SVG <title>. Browser-native, robust,
 * zero state. Cells with missing data still carry an informative
 * tooltip (platform-does-not-carry vs no-recent-snapshot) so the
 * grayed cell isn't editorially silent.
 *
 * Quietness caption: when payload.is_quiet, the soft caption "Markets
 * are quiet — no significant moves in 24h" appears below the grid.
 * The grid still renders (per Panel 8 editorial decision) — the
 * caption tells the reader to interpret the cool colors honestly
 * rather than as a broken viz.
 */

(function () {
  "use strict";

  // Grid geometry — sizes in viewBox-space pixels at natural width.
  // The browser scales the SVG to its container.
  const PLATFORM_COL_WIDTH = 140; // left column for platform labels
  const HEADER_ROW_HEIGHT = 70; // top row for question short_labels
  const CELL_WIDTH = 120;
  const CELL_HEIGHT = 64;
  const CELL_PADDING = 4;

  // Color-scale floor — if the biggest move of the day is under 1¢, we
  // still want the scale anchored to a sensible baseline. Otherwise a
  // 0.3¢ move would render as the deepest red and mislead the reader.
  const MIN_SCALE_RANGE_CENTS = 1.0;

  document.addEventListener("DOMContentLoaded", function () {
    const readoutEl = document.getElementById("heat-map-readout");
    const emptyEl = document.getElementById("heat-map-empty");
    const framingEl = document.getElementById("heat-map-framing");
    const captionEl = document.getElementById("heat-map-caption");

    if (!readoutEl || !emptyEl) {
      return;
    }

    fetch("/api/v1/heat-map/prediction-markets", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("heat-map " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        if (framingEl && payload.framing) {
          framingEl.textContent = payload.framing;
        }
        if (
          !payload.platforms ||
          payload.platforms.length === 0 ||
          !payload.questions ||
          payload.questions.length === 0
        ) {
          showEmpty(
            emptyEl,
            readoutEl,
            "No prediction-market questions configured yet.",
          );
          return;
        }
        renderGrid(readoutEl, emptyEl, payload);
        renderCaption(captionEl, payload);
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("heat-map fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Heat-map feed unavailable — try refreshing.</p>';
        readoutEl.classList.add("hidden");
      });
  });

  // --- Rendering ---------------------------------------------------------

  function renderGrid(readoutEl, emptyEl, payload) {
    const { platforms, questions, cells } = payload;

    // Index cells by (platform, question_id) for O(1) lookup during
    // the row × column iteration below.
    const cellMap = new Map();
    for (const c of cells) {
      cellMap.set(c.platform + "|" + c.question_id, c);
    }

    // Single shared scale: maximum |delta| across cells with data
    // becomes the scale's deep end. Floor at MIN_SCALE_RANGE_CENTS so
    // a quiet day's 0.3¢ move doesn't render as the deepest red.
    let maxAbsDelta = 0;
    for (const c of cells) {
      if (c.delta_24h != null && Math.abs(c.delta_24h) > maxAbsDelta) {
        maxAbsDelta = Math.abs(c.delta_24h);
      }
    }
    if (maxAbsDelta < MIN_SCALE_RANGE_CENTS) {
      maxAbsDelta = MIN_SCALE_RANGE_CENTS;
    }

    const totalWidth = PLATFORM_COL_WIDTH + questions.length * CELL_WIDTH;
    const totalHeight = HEADER_ROW_HEIGHT + platforms.length * CELL_HEIGHT;

    let svg =
      '<svg viewBox="0 0 ' +
      totalWidth +
      " " +
      totalHeight +
      '" ' +
      'preserveAspectRatio="xMidYMid meet" ' +
      'class="w-full" role="img" ' +
      'aria-label="Prediction-market heat-map: day-over-day price moves by platform">';

    svg += renderHeaderRow(questions);
    for (let i = 0; i < platforms.length; i++) {
      svg += renderPlatformRow(
        platforms[i],
        i,
        questions,
        cellMap,
        maxAbsDelta,
      );
    }

    svg += "</svg>";
    readoutEl.innerHTML = svg;
    readoutEl.classList.remove("hidden");
    emptyEl.classList.add("hidden");
  }

  function renderHeaderRow(questions) {
    let html = "";
    for (let j = 0; j < questions.length; j++) {
      const q = questions[j];
      const cx = PLATFORM_COL_WIDTH + j * CELL_WIDTH + CELL_WIDTH / 2;
      const label = q.short_label;
      // Two-line column header when the label has a natural space —
      // keeps "Count ≥5" on one line but breaks "1st by Aug 1" cleanly.
      const lines = splitTwoLines(label, 12);
      const baseY = HEADER_ROW_HEIGHT - 24;
      for (let k = 0; k < lines.length; k++) {
        html +=
          '<text x="' +
          cx +
          '" y="' +
          (baseY + k * 14) +
          '" ' +
          'text-anchor="middle" font-size="11" fill="#475569" ' +
          'font-weight="500" font-family="ui-sans-serif, system-ui">' +
          escapeHtml(lines[k]) +
          "</text>";
      }
      // Category caption under the label — tiny, in lighter gray.
      html +=
        '<text x="' +
        cx +
        '" y="' +
        (HEADER_ROW_HEIGHT - 6) +
        '" ' +
        'text-anchor="middle" font-size="9" fill="#94a3b8" ' +
        'font-family="ui-sans-serif, system-ui" font-style="italic">' +
        escapeHtml(q.category) +
        "</text>";
    }
    return html;
  }

  function renderPlatformRow(platform, rowIndex, questions, cellMap, maxAbsDelta) {
    const y = HEADER_ROW_HEIGHT + rowIndex * CELL_HEIGHT;
    let html = "";

    // Platform label (left column).
    html +=
      '<text x="' +
      (PLATFORM_COL_WIDTH - 12) +
      '" y="' +
      (y + CELL_HEIGHT / 2 + 5) +
      '" ' +
      'text-anchor="end" font-size="13" fill="#334155" font-weight="600" ' +
      'font-family="ui-sans-serif, system-ui">' +
      escapeHtml(platformDisplayName(platform)) +
      "</text>";

    // Cells.
    for (let j = 0; j < questions.length; j++) {
      const q = questions[j];
      const cell = cellMap.get(platform + "|" + q.id);
      const x = PLATFORM_COL_WIDTH + j * CELL_WIDTH;
      html += renderCell(cell, q, x, y, maxAbsDelta);
    }
    return html;
  }

  function renderCell(cell, q, x, y, maxAbsDelta) {
    // Safeguard — service should always return a cell for every
    // (platform, question) pair, but guard against partial payloads.
    if (!cell) {
      return "";
    }

    const w = CELL_WIDTH - CELL_PADDING * 2;
    const h = CELL_HEIGHT - CELL_PADDING * 2;
    const rx = x + CELL_PADDING;
    const ry = y + CELL_PADDING;
    const cx = rx + w / 2;
    const cy = ry + h / 2;

    const color = cellColor(cell, maxAbsDelta);
    const tooltip = cellTooltip(cell, q);

    let html = "<g>";
    // Native browser tooltip — appears on hover, no JS state.
    html += "<title>" + escapeHtml(tooltip) + "</title>";
    html +=
      '<rect x="' +
      rx +
      '" y="' +
      ry +
      '" width="' +
      w +
      '" height="' +
      h +
      '" rx="3" ry="3" fill="' +
      color +
      '" stroke="#e2e8f0" stroke-width="1" />';

    if (cell.has_data) {
      // Yes price — hero number in the cell.
      html +=
        '<text x="' +
        cx +
        '" y="' +
        (cy - 4) +
        '" text-anchor="middle" ' +
        'font-size="16" font-weight="600" fill="#1e293b" ' +
        'font-family="ui-monospace, SFMono-Regular">' +
        formatCents(cell.yes_price) +
        "¢</text>";
      // Delta (or "no Δ" when there's no comparison snapshot).
      if (cell.delta_24h != null) {
        html +=
          '<text x="' +
          cx +
          '" y="' +
          (cy + 13) +
          '" text-anchor="middle" ' +
          'font-size="11" fill="#475569" ' +
          'font-family="ui-monospace, SFMono-Regular">' +
          formatDelta(cell.delta_24h) +
          "</text>";
      } else {
        html +=
          '<text x="' +
          cx +
          '" y="' +
          (cy + 13) +
          '" text-anchor="middle" ' +
          'font-size="10" fill="#94a3b8" font-style="italic" ' +
          'font-family="ui-sans-serif, system-ui">no Δ</text>';
      }
    } else {
      // Missing cell — em dash. Tooltip still tells the reader why.
      html +=
        '<text x="' +
        cx +
        '" y="' +
        (cy + 6) +
        '" text-anchor="middle" ' +
        'font-size="18" fill="#cbd5e1" ' +
        'font-family="ui-sans-serif, system-ui">—</text>';
    }

    html += "</g>";
    return html;
  }

  function cellColor(cell, maxAbsDelta) {
    if (!cell.has_data) {
      // Slightly different gray for empty cells vs has-data-but-no-delta
      // — gives a subtle visual cue without being shouty.
      return "#f8fafc";
    }
    if (cell.delta_24h == null) {
      return "#f1f5f9"; // neutral gray when we have a price but no comparison
    }
    const intensity = Math.min(1.0, Math.abs(cell.delta_24h) / maxAbsDelta);
    if (cell.delta_24h > 0) {
      // Red scale — fef2f2 (light) → fecaca (deep).
      const r = 254;
      const g = Math.round(242 - 116 * intensity);
      const b = Math.round(242 - 116 * intensity);
      return "rgb(" + r + "," + g + "," + b + ")";
    } else if (cell.delta_24h < 0) {
      // Blue scale — eff6ff (light) → bfdbfe (deep).
      const r = Math.round(239 - 48 * intensity);
      const g = Math.round(246 - 27 * intensity);
      const b = 254;
      return "rgb(" + r + "," + g + "," + b + ")";
    }
    return "#f1f5f9"; // exactly zero delta
  }

  function cellTooltip(cell, q) {
    const lines = [q.long_label, "Platform: " + platformDisplayName(cell.platform)];
    if (cell.has_data) {
      lines.push("Yes price: " + formatCents(cell.yes_price) + "¢");
      if (cell.delta_24h != null) {
        lines.push("24h move: " + formatDelta(cell.delta_24h));
      } else {
        lines.push("24h move: not enough history yet");
      }
      if (cell.volume_24h != null) {
        lines.push("24h volume: $" + formatVolume(cell.volume_24h));
      }
    } else if (cell.missing_reason === "platform_does_not_carry") {
      lines.push(
        platformDisplayName(cell.platform) + " does not carry this market",
      );
    } else if (cell.missing_reason === "no_recent_snapshot") {
      lines.push(
        "No recent snapshot — market may be new or scraper may be down",
      );
    }
    return lines.join("\n");
  }

  function renderCaption(captionEl, payload) {
    if (!captionEl) {
      return;
    }
    if (payload.is_quiet) {
      captionEl.textContent =
        "Markets are quiet — no significant moves in 24h.";
      captionEl.classList.remove("hidden");
    } else {
      captionEl.textContent = "";
      captionEl.classList.add("hidden");
    }
  }

  // --- Helpers -----------------------------------------------------------

  function platformDisplayName(p) {
    if (p === "kalshi") return "Kalshi";
    if (p === "polymarket") return "Polymarket";
    if (p === "predictit") return "PredictIt";
    return p;
  }

  function formatCents(price) {
    return Number(price).toFixed(0);
  }

  function formatDelta(delta) {
    const sign = delta > 0 ? "+" : "";
    return sign + Number(delta).toFixed(1) + "¢";
  }

  function formatVolume(v) {
    if (v >= 1000000) {
      return (v / 1000000).toFixed(1) + "M";
    }
    if (v >= 1000) {
      return (v / 1000).toFixed(1) + "K";
    }
    return Math.round(v).toString();
  }

  function splitTwoLines(label, maxPerLine) {
    // Two-line wrap on the first space if the label exceeds the
    // per-line cap. Single-line labels pass through unchanged.
    if (label.length <= maxPerLine) {
      return [label];
    }
    const spaceIdx = label.lastIndexOf(" ", maxPerLine);
    if (spaceIdx <= 0) {
      // No good break point — render as single line; truncation will
      // be visible but tooltip still carries long_label.
      return [label];
    }
    return [label.slice(0, spaceIdx), label.slice(spaceIdx + 1)];
  }

  function showEmpty(emptyEl, readoutEl, message) {
    emptyEl.innerHTML = '<p class="text-sm">' + escapeHtml(message) + "</p>";
    emptyEl.classList.remove("hidden");
    readoutEl.classList.add("hidden");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
