/**
 * Panel 6 — "What changed today" loader.
 *
 * Reads /api/v1/changes/today and renders a small grouped readout:
 * one section per category (storms, equities, cat bond proxy), each
 * with one or more headline lines. Quiet days collapse to a single
 * "Quiet day" message rather than a list of zeros — no fake activity.
 *
 * Visual treatment:
 *   - Section labels in small uppercase (matches the panel-heading
 *     family used elsewhere on the page).
 *   - Each line is a single sentence. No icons, no badges — the panel
 *     is text-dense and a clean typographic hierarchy reads better
 *     than visual decoration at this density.
 *   - Equity / cat bond moves use the same green/red change colors
 *     the equity grid uses, so a glance across panels feels coherent.
 *
 * No charting, no Leaflet, no client-side state beyond the initial
 * fetch. Dashboard refresh = page reload, same as Panel 2 and 3.
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const readoutEl = document.getElementById("changes-readout");
    const emptyEl = document.getElementById("changes-empty");
    const asOfEl = document.getElementById("changes-as-of");

    if (!readoutEl || !emptyEl) {
      return;
    }

    fetch("/api/v1/changes/today", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("changes-feed " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        renderReadout(readoutEl, emptyEl, payload || {});
        if (asOfEl && payload && payload.as_of) {
          asOfEl.textContent = "as of " + payload.as_of.slice(11, 16) + "Z";
          asOfEl.title = payload.as_of;
        }
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("changes fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Today\'s changes feed unavailable — try refreshing.</p>';
        readoutEl.classList.add("hidden");
      });
  });

  // --- Rendering --------------------------------------------------------

  function renderReadout(readoutEl, emptyEl, payload) {
    const sections = [];

    const storms = payload.storms || [];
    if (storms.length > 0) {
      sections.push(buildSection("Storms", storms.map(buildStormLine)));
    }

    // Day 41: equities + cat_bond render as a diverging "Risk Tape" —
    // bars extend left from a center axis for negative moves and right
    // for positive, sorted spectrum-style (most-negative top → most-
    // positive bottom). Bar widths share a single max-absolute scale
    // computed across BOTH equity movers and the cat bond row, so the
    // cat bond's bar reads correctly relative to the day's biggest
    // equity move rather than being pinned to 100% width with n=1.
    const equities = payload.equities || [];
    const catBond = payload.cat_bond;
    const directionalMoves = collectDirectionalMoves(equities, catBond);
    if (directionalMoves.maxAbs > 0) {
      // Axis labels rendered once at the top; section subheaders
      // re-anchor below for "Top equity movers" then "Cat bond proxy".
      sections.push(buildRiskTapeAxisHeader());
      if (equities.length > 0) {
        sections.push(buildRiskTapeSection(
          "Top equity movers",
          sortSpectrum(equities),
          directionalMoves.maxAbs,
        ));
      }
      if (catBond) {
        sections.push(buildRiskTapeSection(
          "Cat bond proxy",
          [catBond],
          directionalMoves.maxAbs,
        ));
      }
    }

    const predictionMarkets = payload.prediction_markets || [];
    if (predictionMarkets.length > 0) {
      sections.push(
        buildSection("Prediction markets", predictionMarkets.map(buildPredictionMarketLine)),
      );
    }

    if (sections.length === 0) {
      // Honest empty state. Better than a fake "all unchanged" item.
      emptyEl.innerHTML =
        '<p class="text-sm">Quiet day — no notable shifts in storms, equities, cat bond pricing, or prediction markets.</p>';
      emptyEl.classList.remove("hidden");
      readoutEl.classList.add("hidden");
      return;
    }

    readoutEl.innerHTML = sections.join("");
    readoutEl.classList.remove("hidden");
    emptyEl.classList.add("hidden");
  }

  // --- Risk Tape (Day 41) ---------------------------------------------

  function collectDirectionalMoves(equities, catBond) {
    // Single shared max-absolute change_percent across equities + cat
    // bond, so bar widths are comparable across both sub-sections. We
    // ignore items with a non-numeric change_percent (shouldn't happen
    // in practice — server filters those out — but defensive in case
    // an upstream shape change leaks one through).
    let maxAbs = 0;
    for (const e of equities || []) {
      if (typeof e.change_percent === "number") {
        maxAbs = Math.max(maxAbs, Math.abs(e.change_percent));
      }
    }
    if (catBond && typeof catBond.change_percent === "number") {
      maxAbs = Math.max(maxAbs, Math.abs(catBond.change_percent));
    }
    return { maxAbs };
  }

  function sortSpectrum(items) {
    // Spectrum sort: most-negative first → most-positive last. Stable
    // for ties so the server's natural ordering breaks them
    // deterministically.
    return items.slice().sort(function (a, b) {
      const ax = typeof a.change_percent === "number" ? a.change_percent : 0;
      const bx = typeof b.change_percent === "number" ? b.change_percent : 0;
      return ax - bx;
    });
  }

  function buildRiskTapeAxisHeader() {
    // Axis labels above the first directional section. Single render
    // even when both equities + cat bond sections show, so the eye
    // anchors once and reads the whole tape against one ruler.
    return (
      '<div class="grid items-center mb-1" ' +
        'style="grid-template-columns: 110px 1fr 1fr 110px; column-gap: 6px;">' +
        '<div></div>' +
        '<div class="text-[10px] text-slate-400 text-right font-mono">← negative</div>' +
        '<div class="text-[10px] text-slate-400 text-left font-mono">positive →</div>' +
        '<div></div>' +
      "</div>"
    );
  }

  function buildRiskTapeSection(label, sortedItems, sharedMaxAbs) {
    const rows = sortedItems.map(function (item) {
      return buildRiskTapeRow(item, sharedMaxAbs);
    });
    return (
      '<div class="mb-3 last:mb-0">' +
        '<div class="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">' +
          escapeHtml(label) +
        "</div>" +
        rows.join("") +
      "</div>"
    );
  }

  function buildRiskTapeRow(item, sharedMaxAbs) {
    // item: { ticker, name, change_percent, headline, ... }
    const change = typeof item.change_percent === "number" ? item.change_percent : 0;
    const isNegative = change < 0;
    const widthPct = sharedMaxAbs > 0
      ? (Math.abs(change) / sharedMaxAbs) * 100
      : 0;
    const ticker = item.ticker || "";
    const sign = change >= 0 ? "+" : "";
    const pctText = sign + change.toFixed(2) + "%";
    // Day 44: brand-friendly direction colors. Brick #7A2E1E for
    // negative (matches --rmn-tier-severe), forest #3B6D11 for
    // positive — both work against the cream background where
    // emerald + rose felt neon.
    const pctColorStyle = change >= 0
      ? "color: #3B6D11;"
      : "color: #7A2E1E;";

    // Day 44: bars in terracotta-brick #7A2E1E (matches --rmn-tier-severe
    // and Risk Tape negative percent text). Tighter thematic match to
    // the rest of the brand palette than the previous charcoal.
    const bar = '<div style="height: 8px; width: ' +
                widthPct.toFixed(1) + '%; background: #7A2E1E;"></div>';

    // Day 44: axis line picks up the brand divider color rather than
    // the previous neutral slate-300, threading the band visually
    // into the rest of the cream palette.
    const negCell = isNegative
      ? '<div class="flex justify-end pr-1" ' +
          'style="border-right: 1px solid var(--rmn-divider);">' + bar + "</div>"
      : '<div style="border-right: 1px solid var(--rmn-divider);"></div>';
    const posCell = isNegative
      ? '<div></div>'
      : '<div class="flex justify-start pl-1">' + bar + "</div>";

    const leftLabel = isNegative
      ? '<span class="font-mono text-right text-sm" style="color: #1A1A1A;">' +
          escapeHtml(ticker) + ' <span style="' + pctColorStyle + '">' + escapeHtml(pctText) + "</span>" +
        "</span>"
      : "<span></span>";
    const rightLabel = isNegative
      ? "<span></span>"
      : '<span class="font-mono text-sm" style="color: #1A1A1A;">' +
          '<span style="' + pctColorStyle + '">' + escapeHtml(pctText) + "</span> " + escapeHtml(ticker) +
        "</span>";

    return (
      '<div class="grid items-center py-1" ' +
        'style="grid-template-columns: 110px 1fr 1fr 110px; column-gap: 6px;">' +
        leftLabel +
        negCell +
        posCell +
        rightLabel +
      "</div>"
    );
  }

  function buildSection(label, lines) {
    return (
      '<div class="mb-3 last:mb-0">' +
        '<div class="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">' +
          escapeHtml(label) +
        "</div>" +
        '<ul class="space-y-1 text-sm text-slate-700">' +
          lines.map(function (line) {
            return '<li class="leading-snug">' + line + "</li>";
          }).join("") +
        "</ul>" +
      "</div>"
    );
  }

  function buildStormLine(item) {
    // Storm headlines are pre-narrated server-side ("Foo intensified
    // +15 kt to 95 kt."). We just render them plain.
    return escapeHtml(item.headline || item.name || "Storm change");
  }

  // Day 41: buildEquityLine and buildCatBondLine were removed when the
  // text-based equity/cat-bond headlines were replaced by the diverging
  // Risk Tape rows. Equity and cat bond rendering now flows through
  // buildRiskTapeRow / buildRiskTapeSection above. Prediction-market
  // rendering stays in the original buildSection + buildPredictionMarketLine
  // pattern below since volume isn't directional and a center-axis
  // treatment would mis-encode the data.

  function buildPredictionMarketLine(item) {
    // Prediction-market headlines come pre-narrated as
    // "$5,442 traded on Polymarket — Will a hurricane form by May 31?".
    // Volume isn't directional (no green/red), so the leading dollar
    // amount + platform stays neutral mono and the tail (the question
    // itself) gets the same lighter slate treatment as cat bond /
    // equity headlines for visual consistency across the panel.
    const headline = String(item.headline || "");
    const dashIdx = headline.indexOf("—");
    if (dashIdx === -1) {
      return escapeHtml(headline);
    }
    const lead = headline.slice(0, dashIdx).trim();
    const tail = headline.slice(dashIdx).trim();
    return (
      '<span class="font-mono text-slate-700">' + escapeHtml(lead) + "</span> " +
      '<span class="text-slate-500">' + escapeHtml(tail) + "</span>"
    );
  }

  // Day 41 removed the standalone changeColor helper. It was used only
  // by buildEquityLine / buildCatBondLine, both of which were replaced
  // by the Risk Tape renderer (which inlines the same emerald/rose
  // class choice in buildRiskTapeRow). Kept as a comment marker so a
  // future text-fallback variant can re-introduce it without
  // re-deriving the convention.

  // --- Helpers ----------------------------------------------------------

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
