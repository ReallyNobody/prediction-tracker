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

    // Two distinct intensity readings live in the analog data:
    //   - saffir_simpson_at_landfall — the Cat the storm was at *landfall*
    //   - peak_kt — the storm's *lifetime peak* sustained winds
    // These are often different (Milton 2024: Cat 3 at landfall, peaked
    // Cat 5 / 155 kt). Rendering "Cat 3 · 155 kt peak" side-by-side made
    // them read as the same metric. Qualify both explicitly, and derive
    // the peak's Saffir-Simpson category so the reader sees the gap at
    // a glance ("Cat 3 at landfall · peak Cat 5 (155 kt)").
    const cat =
      typeof entry.saffir_simpson_at_landfall === "number"
        ? "Cat " + entry.saffir_simpson_at_landfall + " at landfall"
        : "";
    const peakKt =
      typeof entry.peak_kt === "number" ? formatPeak(entry.peak_kt) : "";
    const factsParts = [];
    if (cat) factsParts.push(cat);
    if (entry.landfall_state) {
      // "Cat X at landfall" above already establishes the landfall
      // context, so the state stands alone — avoids "landfall" twice
      // in the same facts line.
      factsParts.push(escapeHtml(entry.landfall_state));
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

  /**
   * Render the peak-intensity facts token. Derives the Saffir-Simpson
   * category from the peak knots so the reader sees the peak's Cat
   * explicitly — important because peak Cat often differs from the
   * Cat at landfall (Milton 2024 peaked Cat 5 then landed as Cat 3).
   *
   * Saffir-Simpson breakpoints (in knots, per NHC):
   *   Cat 1: 64-82, Cat 2: 83-95, Cat 3: 96-112, Cat 4: 113-136, Cat 5: 137+
   *
   * Below TS-force (34 kt) we fall back to a bare-kt token rather than
   * inventing a Cat label that doesn't exist on the scale.
   */
  function formatPeak(peakKt) {
    const cat = peakKtToCat(peakKt);
    if (cat === null) {
      return "peak " + peakKt + " kt";
    }
    return "peak Cat " + cat + " (" + peakKt + " kt)";
  }

  function peakKtToCat(kt) {
    if (kt >= 137) return 5;
    if (kt >= 113) return 4;
    if (kt >= 96) return 3;
    if (kt >= 83) return 2;
    if (kt >= 64) return 1;
    return null;
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
