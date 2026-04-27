/**
 * Panel 5 — "Historical analogs" loader.
 *
 * Reads /api/v1/analogs and renders a 1-3 column grid of analog cards.
 * The server's "framing" string (which differs by mode — "Most similar
 * past landfalls to today's forecast" vs. "Recent major Atlantic
 * storms") goes in the panel header so readers know what they're
 * looking at without us having to duplicate the mode logic on the
 * client.
 *
 * Each card renders:
 *   - storm name + year
 *   - peak intensity + Saffir-Simpson at landfall
 *   - landfall state
 *   - approximate insured loss
 *   - 2-3 line narrative caption
 *   - distance-from-active-cone in km (active mode only)
 *
 * No charts, no Leaflet — this is the most text-heavy panel on the
 * page and a typographic hierarchy reads better than a visualization
 * at this density.
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const readoutEl = document.getElementById("analogs-readout");
    const emptyEl = document.getElementById("analogs-empty");
    const framingEl = document.getElementById("analogs-framing");

    if (!readoutEl || !emptyEl) {
      return;
    }

    fetch("/api/v1/analogs", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("analogs-feed " + r.status);
        }
        return r.json();
      })
      .then(function (payload) {
        const analogs = (payload && payload.analogs) || [];
        if (analogs.length === 0) {
          showEmpty(emptyEl, readoutEl, "No historical analogs in the dataset.");
          return;
        }
        if (framingEl && payload.framing) {
          framingEl.textContent = payload.framing;
        }
        renderReadout(readoutEl, emptyEl, analogs);
      })
      .catch(function (err) {
        // eslint-disable-next-line no-console
        console.error("analogs fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Analogs feed unavailable — try refreshing.</p>';
        readoutEl.classList.add("hidden");
      });
  });

  // --- Rendering --------------------------------------------------------

  function renderReadout(readoutEl, emptyEl, analogs) {
    readoutEl.innerHTML = analogs.map(buildCard).join("");
    readoutEl.classList.remove("hidden");
    emptyEl.classList.add("hidden");
  }

  function buildCard(entry) {
    const titleLine =
      escapeHtml(entry.name || "Unnamed storm") +
      ' <span class="font-mono text-slate-500">' +
      escapeHtml(String(entry.year || "")) +
      "</span>";

    const cat =
      typeof entry.saffir_simpson_at_landfall === "number"
        ? "Cat " + entry.saffir_simpson_at_landfall
        : "";
    const peakKt =
      typeof entry.peak_kt === "number" ? entry.peak_kt + " kt peak" : "";
    const factsParts = [];
    if (cat) factsParts.push(cat);
    if (entry.landfall_state) {
      factsParts.push("landfall " + escapeHtml(entry.landfall_state));
    }
    if (peakKt) factsParts.push(peakKt);
    if (typeof entry.distance_km === "number") {
      factsParts.push(entry.distance_km + " km from today's cone");
    }
    const factsLine = factsParts.join('<span class="text-slate-300"> · </span>');

    const lossText =
      typeof entry.insured_loss_usd_billions === "number"
        ? "≈$" +
          Number(entry.insured_loss_usd_billions).toFixed(1) +
          "B insured"
        : "";

    return (
      '<div class="rounded border border-slate-200 bg-white p-3"' +
      ' data-name="' + escapeHtml(entry.name) + '"' +
      ' data-year="' + escapeHtml(String(entry.year)) + '">' +
        '<div class="text-sm font-semibold text-slate-900">' +
          titleLine +
        "</div>" +
        '<div class="mt-1 text-[11px] text-slate-500">' +
          factsLine +
        "</div>" +
        (lossText
          ? '<div class="mt-1 text-xs font-mono text-slate-700">' +
              lossText +
            "</div>"
          : "") +
        '<p class="mt-2 text-xs text-slate-600 leading-relaxed">' +
          escapeHtml(entry.narrative || "") +
        "</p>" +
      "</div>"
    );
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
