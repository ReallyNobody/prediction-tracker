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
          '<p class="text-sm text-rose-500">Daily delta feed unavailable — try refreshing.</p>';
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

    const equities = payload.equities || [];
    if (equities.length > 0) {
      sections.push(
        buildSection("Top equity movers", equities.map(buildEquityLine)),
      );
    }

    const catBond = payload.cat_bond;
    if (catBond) {
      sections.push(buildSection("Cat bond proxy", [buildCatBondLine(catBond)]));
    }

    if (sections.length === 0) {
      // Honest empty state. Better than a fake "all unchanged" item.
      emptyEl.innerHTML =
        '<p class="text-sm">Quiet day — no notable shifts in storms, equities, or cat bond pricing.</p>';
      emptyEl.classList.remove("hidden");
      readoutEl.classList.add("hidden");
      return;
    }

    readoutEl.innerHTML = sections.join("");
    readoutEl.classList.remove("hidden");
    emptyEl.classList.add("hidden");
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

  function buildEquityLine(item) {
    // Equity headlines come pre-narrated as "UVE +4.20% — name".
    // Color the leading ticker+pct based on the change_percent sign.
    const change = item.change_percent;
    const colorClass = changeColor(change);
    const headline = String(item.headline || "");
    // Split on the em-dash separator the server uses: "TICKER ±%.%% — name".
    const dashIdx = headline.indexOf("—");
    if (dashIdx === -1) {
      return escapeHtml(headline);
    }
    const lead = headline.slice(0, dashIdx).trim();
    const tail = headline.slice(dashIdx).trim();  // includes the em-dash
    return (
      '<span class="font-mono ' + colorClass + '">' + escapeHtml(lead) + "</span> " +
      '<span class="text-slate-500">' + escapeHtml(tail) + "</span>"
    );
  }

  function buildCatBondLine(item) {
    const change = item.change_percent;
    const colorClass = changeColor(change);
    const headline = String(item.headline || "");
    const dashIdx = headline.indexOf("—");
    if (dashIdx === -1) {
      return escapeHtml(headline);
    }
    const lead = headline.slice(0, dashIdx).trim();
    const tail = headline.slice(dashIdx).trim();
    return (
      '<span class="font-mono ' + colorClass + '">' + escapeHtml(lead) + "</span> " +
      '<span class="text-slate-500">' + escapeHtml(tail) + "</span>"
    );
  }

  function changeColor(change) {
    if (typeof change !== "number") return "text-slate-700";
    if (change >= 0) return "text-emerald-600";
    return "text-rose-600";
  }

  // --- Helpers ----------------------------------------------------------

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
