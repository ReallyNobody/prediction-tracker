/**
 * Panel 2 — "Companies on the line" loader.
 *
 * Fetches /api/v1/quotes/hurricane-universe (one row per universe ticker
 * with its latest quote, or quote=null when no scrape has produced one
 * yet) and renders the compact ticker grid.
 *
 * Two interactive layers:
 *
 *   * Sector filter pills — toggled client-side; one fetch hydrates the
 *     full universe, button clicks just hide/show subsets. Avoids the
 *     re-fetch flicker the API-side ?sectors= param would introduce.
 *
 *   * Cone-overlap highlight — when /api/v1/forecasts/active reports
 *     active storms, we extract the affected states from the cone
 *     polygon's bounding box, and tag tickers whose key_states intersect
 *     that set with a "in-cone" class. Off-season this is a no-op.
 *
 * No Leaflet here — Panel 2 is just DOM. We do a one-time fetch on page
 * load and let the user re-load to refresh; future work can poll on a
 * timer if we want it self-updating, but at 15-min Yahoo delay, page
 * reload is honest.
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const gridEl = document.getElementById("equities-grid");
    const emptyEl = document.getElementById("equities-empty");
    const asOfEl = document.getElementById("equities-as-of");
    const pillsEl = document.getElementById("equities-sector-pills");

    // Panel 2 only exists on the dashboard index. Bail early on any
    // page that loads this script (via base.html) but skips the grid.
    if (!gridEl || !emptyEl || !pillsEl) {
      return;
    }

    // Two parallel fetches: the universe quote payload + the active
    // forecast (for cone-overlap highlight). Forecast may 4xx/5xx or
    // return zero storms; that's a non-event for Panel 2 — we just
    // skip the highlight in that case.
    const quotesPromise = fetch("/api/v1/quotes/hurricane-universe", {
      headers: { Accept: "application/json" },
    }).then(function (r) {
      if (!r.ok) {
        throw new Error("quotes-feed " + r.status);
      }
      return r.json();
    });

    const forecastPromise = fetch("/api/v1/forecasts/active", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("forecast-feed " + r.status);
        }
        return r.json();
      })
      .catch(function () {
        // Don't let a forecast outage break the equity grid.
        return { storms: [] };
      });

    Promise.all([quotesPromise, forecastPromise])
      .then(function (results) {
        const quotePayload = results[0] || { tickers: [] };
        const forecastPayload = results[1] || { storms: [] };

        const tickers = quotePayload.tickers || [];
        if (tickers.length === 0) {
          showEmpty(emptyEl, gridEl, "No tickers in the universe yet.");
          return;
        }

        const inConeStates = statesFromForecast(forecastPayload.storms || []);
        renderGrid(gridEl, emptyEl, tickers, inConeStates);
        updateAsOfReadout(asOfEl, tickers);
        wireUpPills(pillsEl, gridEl);
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("equities fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Equities feed unavailable — try refreshing.</p>';
        gridEl.classList.add("hidden");
      });
  });

  // --- Cone-overlap helper ----------------------------------------------

  /**
   * Returns the set of US state abbrs whose territory intersects any
   * storm's forecast cone.
   *
   * Keeps the logic deliberately coarse: we intersect the cone's
   * bounding box (min/max lat+lon) against a hand-tuned table of
   * Atlantic / Gulf state lat/lon footprints. Good enough for "the
   * cone is somewhere over Florida and Georgia"; not good enough for
   * a bona fide GIS join, which is overkill at this resolution.
   */
  const COASTAL_STATE_BOXES = {
    // [minLat, maxLat, minLon, maxLon] — inland states with non-zero
    // hurricane exposure are intentionally generous; reinsurers don't
    // care about state-level precision so getting Mississippi roughly
    // right is fine.
    TX: [25.84, 36.5, -106.65, -93.5],
    LA: [28.93, 33.02, -94.04, -88.82],
    MS: [30.17, 34.99, -91.66, -88.1],
    AL: [30.13, 35.01, -88.47, -84.89],
    FL: [24.4, 31.0, -87.63, -79.97],
    GA: [30.36, 35.0, -85.6, -80.84],
    SC: [32.03, 35.2, -83.35, -78.54],
    NC: [33.84, 36.59, -84.32, -75.46],
    VA: [36.54, 39.47, -83.68, -75.24],
    MD: [37.89, 39.72, -79.49, -75.05],
    DE: [38.45, 39.84, -75.79, -75.05],
    NJ: [38.93, 41.36, -75.56, -73.89],
    NY: [40.5, 45.02, -79.76, -71.86],
    CT: [40.99, 42.05, -73.73, -71.78],
    RI: [41.15, 42.02, -71.86, -71.12],
    MA: [41.24, 42.89, -73.51, -69.93],
    NH: [42.7, 45.31, -72.56, -70.61],
    ME: [43.06, 47.46, -71.08, -66.95],
    PA: [39.72, 42.27, -80.52, -74.69],
    KY: [36.5, 39.15, -89.57, -81.96],
    AR: [33.0, 36.5, -94.62, -89.64],
    TN: [34.98, 36.68, -90.31, -81.65],
    PR: [17.88, 18.52, -67.95, -65.22],
    VI: [17.67, 18.41, -65.04, -64.56],
  };

  function statesFromForecast(storms) {
    const hits = new Set();
    for (const entry of storms) {
      const cone = entry && entry.forecast && entry.forecast.cone_geojson;
      if (!cone || !cone.coordinates || cone.coordinates.length === 0) {
        continue;
      }
      const ring = cone.coordinates[0]; // outer ring; GeoJSON [lon, lat]
      let minLon = Infinity, maxLon = -Infinity;
      let minLat = Infinity, maxLat = -Infinity;
      for (const pt of ring) {
        const lon = pt[0], lat = pt[1];
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
      // Intersect bounding box against each known coastal state box.
      for (const [stateCode, box] of Object.entries(COASTAL_STATE_BOXES)) {
        const sMinLat = box[0], sMaxLat = box[1];
        const sMinLon = box[2], sMaxLon = box[3];
        const overlapsLat = !(maxLat < sMinLat || minLat > sMaxLat);
        const overlapsLon = !(maxLon < sMinLon || minLon > sMaxLon);
        if (overlapsLat && overlapsLon) {
          hits.add(stateCode);
        }
      }
    }
    return hits;
  }

  // --- Grid rendering ----------------------------------------------------

  function renderGrid(gridEl, emptyEl, tickers, inConeStates) {
    const html = tickers.map(function (entry) {
      return tickerTile(entry, inConeStates);
    }).join("");
    gridEl.innerHTML = html;
    gridEl.classList.remove("hidden");
    emptyEl.classList.add("hidden");
  }

  function tickerTile(entry, inConeStates) {
    const inCone = (entry.key_states || []).some(function (s) {
      return inConeStates.has(s);
    });
    const quote = entry.quote;

    const priceBlock = quote
      ? formatPriceBlock(quote)
      : '<span class="text-slate-300 font-mono text-sm">—</span>';

    // Tile container styling:
    //   - rounded card with thin border, hover lifts to slate-50
    //   - if in-cone, add a subtle amber ring to flag the storm exposure
    const ringClass = inCone
      ? "ring-1 ring-amber-300 bg-amber-50"
      : "bg-white";
    const sectorBadge = sectorBadgeHtml(entry.sector);

    return (
      '<div class="rounded border border-slate-200 ' + ringClass +
      ' px-2 py-2 hover:bg-slate-50 transition-colors text-xs"' +
      ' data-ticker="' + escapeHtml(entry.ticker) + '"' +
      ' data-sector="' + escapeHtml(entry.sector) + '"' +
      ' data-in-cone="' + (inCone ? "1" : "0") + '"' +
      ' title="' + escapeHtml(entry.notes || entry.name) + '">' +
        '<div class="flex items-baseline justify-between gap-1">' +
          '<span class="font-mono font-semibold text-slate-900">' +
            escapeHtml(entry.ticker) + "</span>" +
          sectorBadge +
        "</div>" +
        '<div class="text-[11px] text-slate-500 truncate" title="' +
          escapeHtml(entry.name) + '">' +
          escapeHtml(entry.name) + "</div>" +
        '<div class="mt-1 flex items-baseline justify-between">' +
          priceBlock +
        "</div>" +
      "</div>"
    );
  }

  function formatPriceBlock(quote) {
    const last = (quote.last_price !== null && quote.last_price !== undefined)
      ? "$" + Number(quote.last_price).toFixed(2)
      : "—";
    const changePct = quote.change_percent;
    const changeClass = changePct == null
      ? "text-slate-500"
      : changePct >= 0
        ? "text-emerald-600"
        : "text-rose-600";
    const changeText = changePct == null
      ? ""
      : (changePct >= 0 ? "+" : "") + Number(changePct).toFixed(2) + "%";
    return (
      '<span class="font-mono text-sm text-slate-900">' + last + "</span>" +
      '<span class="font-mono text-[11px] ' + changeClass + '">' +
        escapeHtml(changeText) +
      "</span>"
    );
  }

  function sectorBadgeHtml(sector) {
    // Subtle slate badge — sector is shown more for filtering than visual
    // pop, so we keep the color neutral and let the in-cone amber ring
    // be the panel's main signal.
    const label = {
      insurer: "ins",
      reinsurer: "re",
      homebuilder: "hb",
      utility: "util",
    }[sector] || sector;
    return (
      '<span class="text-[10px] uppercase tracking-wide text-slate-400 font-mono">' +
        escapeHtml(label) +
      "</span>"
    );
  }

  // --- as_of readout (latest scrape timestamp) --------------------------

  function updateAsOfReadout(asOfEl, tickers) {
    if (!asOfEl) return;
    let latest = null;
    for (const t of tickers) {
      if (!t.quote || !t.quote.as_of) continue;
      if (latest === null || t.quote.as_of > latest) {
        latest = t.quote.as_of;
      }
    }
    if (latest === null) {
      asOfEl.textContent = "no quote yet";
      return;
    }
    // Render a compact human label. Pull HH:MM Z out of the ISO-8601;
    // good enough for a "delayed by 15 min" panel — full timestamp is
    // available via element title.
    asOfEl.textContent = "as of " + latest.slice(11, 16) + "Z";
    asOfEl.title = latest;
  }

  // --- Sector filter pills -----------------------------------------------

  function wireUpPills(pillsEl, gridEl) {
    pillsEl.addEventListener("click", function (event) {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.dataset.sector) {
        return;
      }
      const sector = target.dataset.sector;
      // Update aria-pressed on the buttons themselves.
      const buttons = pillsEl.querySelectorAll("[data-sector]");
      buttons.forEach(function (btn) {
        btn.setAttribute("aria-pressed", btn === target ? "true" : "false");
      });
      // Show/hide grid tiles matching the sector.
      const tiles = gridEl.querySelectorAll("[data-sector]");
      tiles.forEach(function (tile) {
        const matches = sector === "all" || tile.dataset.sector === sector;
        if (matches) {
          tile.classList.remove("hidden");
        } else {
          tile.classList.add("hidden");
        }
      });
    });
  }

  // --- Empty state + helpers --------------------------------------------

  function showEmpty(emptyEl, gridEl, message) {
    emptyEl.innerHTML = '<p class="text-sm">' + escapeHtml(message) + "</p>";
    emptyEl.classList.remove("hidden");
    gridEl.classList.add("hidden");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
