/**
 * Panel 3 — "Cat bond market" loader.
 *
 * Reads from the same /api/v1/quotes/hurricane-universe endpoint as
 * Panel 2, filtered to sectors=cat_bond_etf. Renders one prominent
 * card per cat bond ETF (today: just ILS) with last price + change %.
 *
 * Why this panel exists:
 *
 *   The institutional cat bond spread benchmarks — Plenum UCITS Cat
 *   Bond Fund Index, Swiss Re's Cat Bond Total Return Index — are all
 *   gated behind paid subscriptions (Bloomberg / Artemis). A
 *   publicly-traded cat bond ETF is the closest free, real-time proxy:
 *   its NAV moves as the underlying cat bond market reprices. Less
 *   precise than the institutional index, but free and journalism-
 *   appropriate.
 *
 * No fancy charting library — a single-tile readout with the price,
 *   colored change, and a tooltip is the whole UI today. If/when we
 *   want a sparkline we can add inline-SVG; the panel is small enough
 *   that we don't need a JS chart library.
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const readoutEl = document.getElementById("cat-bonds-readout");
    const emptyEl = document.getElementById("cat-bonds-empty");
    const asOfEl = document.getElementById("cat-bonds-as-of");

    if (!readoutEl || !emptyEl) {
      return;
    }

    fetch("/api/v1/quotes/hurricane-universe?sectors=cat_bond_etf", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("cat-bonds-feed " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        const tickers = (payload && payload.tickers) || [];
        if (tickers.length === 0) {
          showEmpty(emptyEl, readoutEl, "No cat bond proxies in the universe yet.");
          return;
        }
        renderReadout(readoutEl, emptyEl, tickers);
        updateAsOfReadout(asOfEl, tickers);
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("cat-bonds fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Cat bond feed unavailable — try refreshing.</p>';
        readoutEl.classList.add("hidden");
      });
  });

  // --- Rendering --------------------------------------------------------

  function renderReadout(readoutEl, emptyEl, tickers) {
    readoutEl.innerHTML = tickers.map(buildCard).join("");
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

    // Single prominent card — symbol + name on the left, price + change
    // on the right. Notes flow as supporting copy underneath.
    return (
      '<div class="rounded border border-slate-200 bg-white p-3"' +
      ' data-ticker="' + escapeHtml(entry.ticker) + '"' +
      ' title="' + escapeHtml(entry.name) + '">' +
        '<div class="flex items-baseline justify-between gap-3 flex-wrap">' +
          '<div>' +
            '<div class="font-mono font-semibold text-base text-slate-900">' +
              escapeHtml(entry.ticker) + "</div>" +
            '<div class="text-xs text-slate-500">' +
              escapeHtml(entry.name) + "</div>" +
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
