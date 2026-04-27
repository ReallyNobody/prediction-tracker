/**
 * Panel 3 — "Hurricane risk capital" loader.
 *
 * Renamed Day 20 from panel_cat_bonds.js. Original panel was a single
 * row (ILS, the cat bond ETF). The reframe adds a second row for KBWP
 * (KBW P&C Insurance ETF), giving the panel two views of how hurricane-
 * risk capital is being priced — alternative capital (cat bonds) and
 * listed P&C insurers.
 *
 * Reads from the same /api/v1/quotes/hurricane-universe endpoint Panel
 * 2 uses, filtered to sectors=cat_bond_etf,pc_index. Each ticker
 * renders as a single card with its symbol, name, last price, and
 * day's change. Notes flow as supporting copy underneath each card,
 * which is how the panel explains what each proxy actually represents
 * without burying it in the panel header copy.
 *
 * Why no reinsurance row:
 *
 *   KBW publishes a Global Reinsurance Index, but the publicly
 *   investable products that track it are foreign-listed and thinly
 *   traded — not appropriate as a journalism-grade real-time read.
 *   The individual reinsurer tickers (RNR / EG / ACGL / AXS / MKL /
 *   HG) in Panel 2's reinsurer filter pill cover that layer at
 *   per-name resolution instead.
 *
 * No fancy charting — two stacked cards is the whole UI today. If/when
 * we want a sparkline we can add inline-SVG; the panel is small enough
 * that we don't need a JS chart library.
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const readoutEl = document.getElementById("risk-capital-readout");
    const emptyEl = document.getElementById("risk-capital-empty");
    const asOfEl = document.getElementById("risk-capital-as-of");

    if (!readoutEl || !emptyEl) {
      return;
    }

    fetch("/api/v1/quotes/hurricane-universe?sectors=cat_bond_etf,pc_index", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("risk-capital-feed " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        const tickers = (payload && payload.tickers) || [];
        if (tickers.length === 0) {
          showEmpty(emptyEl, readoutEl, "No risk-capital proxies in the universe yet.");
          return;
        }
        renderReadout(readoutEl, emptyEl, tickers);
        updateAsOfReadout(asOfEl, tickers);
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("risk-capital fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Risk-capital feed unavailable — try refreshing.</p>';
        readoutEl.classList.add("hidden");
      });
  });

  // --- Rendering --------------------------------------------------------

  function renderReadout(readoutEl, emptyEl, tickers) {
    // Universe order is cat_bond_etf first, pc_index second — exactly
    // what we want top-down. Don't re-sort: respecting universe order
    // keeps editorial control in the YAML rather than the JS.
    readoutEl.innerHTML =
      '<div class="space-y-2">' +
      tickers.map(buildCard).join("") +
      "</div>";
    readoutEl.classList.remove("hidden");
    emptyEl.classList.add("hidden");
  }

  function buildCard(entry) {
    const quote = entry.quote;
    const lastPrice =
      quote && typeof quote.last_price === "number"
        ? "$" + Number(quote.last_price).toFixed(2)
        : "—";
    const changePct = quote && typeof quote.change_percent === "number"
      ? quote.change_percent
      : null;
    const changeClass = changePct == null
      ? "text-slate-500"
      : changePct >= 0
        ? "text-emerald-600"
        : "text-rose-600";
    const changeText = changePct == null
      ? ""
      : (changePct >= 0 ? "+" : "") + Number(changePct).toFixed(2) + "%";
    const sectorLabel = sectorLabelHtml(entry.sector);

    // Single card — symbol + name on the left, price + change on the
    // right. Sector label sits as a small uppercase tag under the name
    // so a glance distinguishes the cat bond row from the P&C row
    // without needing a section heading above each card.
    return (
      '<div class="rounded border border-slate-200 bg-white p-3"' +
      ' data-ticker="' + escapeHtml(entry.ticker) + '"' +
      ' data-sector="' + escapeHtml(entry.sector) + '"' +
      ' title="' + escapeHtml(entry.name) + '">' +
        '<div class="flex items-baseline justify-between gap-3 flex-wrap">' +
          '<div>' +
            '<div class="font-mono font-semibold text-base text-slate-900">' +
              escapeHtml(entry.ticker) + "</div>" +
            '<div class="text-xs text-slate-500">' +
              escapeHtml(entry.name) + "</div>" +
            sectorLabel +
          "</div>" +
          '<div class="text-right">' +
            '<div class="font-mono text-base text-slate-900">' + lastPrice + "</div>" +
            '<div class="font-mono text-xs ' + changeClass + '">' +
              escapeHtml(changeText) + "</div>" +
          "</div>" +
        "</div>" +
        (entry.notes
          ? '<p class="mt-2 text-xs text-slate-500 leading-relaxed">' +
              escapeHtml(entry.notes) +
            "</p>"
          : "") +
      "</div>"
    );
  }

  function sectorLabelHtml(sector) {
    // Editorial labels for the two index sectors — the YAML's literal
    // sector strings ("cat_bond_etf", "pc_index") are dev-facing, not
    // reader-facing. Keep this map narrow: only the sectors Panel 3
    // actually renders need a label.
    const label = {
      cat_bond_etf: "Cat bond proxy",
      pc_index: "P&C insurers index",
    }[sector] || sector;
    return (
      '<div class="mt-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">' +
        escapeHtml(label) +
      "</div>"
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
    asOfEl.textContent = "as of " + latest.slice(11, 16) + "Z";
    asOfEl.title = latest;
  }

  // --- Empty state + helpers --------------------------------------------

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
