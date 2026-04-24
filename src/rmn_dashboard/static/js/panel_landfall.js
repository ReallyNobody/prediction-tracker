/* Panel 4b — Landfall probability map.
 *
 * Renders NHC's 5-day cumulative wind-speed probability (WSP) product as
 * a choropleth on a dedicated Leaflet map. For each shaded polygon, the
 * color encodes the probability (%) that sustained winds reach the
 * user-selected threshold — 34 kt (tropical-storm-force), 50 kt
 * (damaging), or 64 kt (hurricane-force) — at that location within the
 * next 120 hours.
 *
 * Why this panel exists alongside Panel 1 (the cone map):
 *   - The cone is a *track uncertainty* visualization: where the storm
 *     center might go.
 *   - WSP is a *wind hazard* visualization: where damaging winds might
 *     actually arrive.
 * A cone clipping the Florida Keys with 10% WSP is a very different
 * underwriting situation than one clipping it with 60%. RMN's readers
 * care about the second framing, and it's worth its own real estate.
 *
 * Data shape (matches rmn_dashboard.scrapers.nhc_shapefiles
 * .parse_wind_probability_zip):
 *
 *   {
 *     "type": "FeatureCollection",
 *     "features": [
 *       {
 *         "type": "Feature",
 *         "geometry": { "type": "Polygon" | "MultiPolygon", "coordinates": ... },
 *         "properties": {
 *           "PWIND": 40,           // lower bound of probability band, 0–100
 *           "threshold_kt": 34     // 34, 50, or 64; added by parser
 *         }
 *       },
 *       ...
 *     ]
 *   }
 *
 * The panel pre-builds one Leaflet layer group per threshold on first
 * render, so switching thresholds is add/remove rather than rebuild —
 * no re-fetch, no GeoJSON parse round-trip, no perceptible flicker.
 */

(function () {
  "use strict";

  // Atlantic basin centroid + initial zoom; actual bounds get refitted
  // from the 34 kt layer on first paint (that layer's envelope is always
  // the widest, since higher-threshold contours nest inside it).
  const DEFAULT_CENTER = [25.0, -70.0];
  const DEFAULT_ZOOM = 4;

  // Probability color ramp — amber through orange to deep red, keyed to
  // the PWIND band each polygon represents. Eight bands is a readability
  // compromise: enough resolution that the 40%→60% boundary (the "start
  // to worry" line for most underwriters) reads distinctly, but not so
  // many that the legend becomes a paint chart. Palette matches the
  // dashboard's orange accent so Panel 1 and Panel 4 feel related.
  const BANDS = [
    { min: 80, label: "≥ 80%", color: "#7c2d12" }, // orange-900
    { min: 60, label: "60 – 80%", color: "#9a3412" }, // orange-800
    { min: 50, label: "50 – 60%", color: "#ea580c" }, // orange-600
    { min: 40, label: "40 – 50%", color: "#f59e0b" }, // amber-500
    { min: 30, label: "30 – 40%", color: "#fbbf24" }, // amber-400
    { min: 20, label: "20 – 30%", color: "#fcd34d" }, // amber-300
    { min: 10, label: "10 – 20%", color: "#fde68a" }, // amber-200
    { min: 0, label: "< 10%", color: "#fef3c7" }, // amber-100
  ];

  const DEFAULT_THRESHOLD = 34;
  const SUPPORTED_THRESHOLDS = [34, 50, 64];

  document.addEventListener("DOMContentLoaded", function () {
    const mapEl = document.getElementById("landfall-map");
    const emptyEl = document.getElementById("landfall-map-empty");
    const thresholdEl = document.getElementById("landfall-threshold");

    // Script is loaded from base.html on every page; guard in case this
    // dashboard-only markup isn't present.
    if (!mapEl || !emptyEl || !thresholdEl) {
      return;
    }

    fetch("/api/v1/forecasts/active?include_wsp=true", {
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        const storms = (payload && payload.storms) || [];
        const features = collectWspFeatures(storms);
        if (features.length === 0) {
          // Two shapes collapse to the same empty-state message: no
          // active storms at all, and active storms whose forecasts
          // don't yet carry WSP (fresh invests, pre-WSP advisories).
          // The user-facing distinction doesn't matter — both mean
          // "nothing to show for landfall probability right now."
          showEmptyState(emptyEl, mapEl);
          return;
        }
        renderMap(mapEl, emptyEl, thresholdEl, features);
      })
      .catch(function (err) {
        // Distinct from the empty state so a broken endpoint during a
        // live incident doesn't silently masquerade as "season off."
        console.error("landfall-map fetch failed", err);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Probability feed unavailable — try refreshing.</p>';
        mapEl.classList.add("hidden");
      });
  });

  function collectWspFeatures(storms) {
    // Each storm's forecast carries its own WSP FeatureCollection. At
    // peak season there can be 2–3 active Atlantic storms; concatenating
    // their polygons is fine and matches how NHC's own basin-scoped
    // wsp_120hr product is already shaped (the product is basin-scoped
    // even though we hang it off individual Forecast rows in the DB).
    const all = [];
    storms.forEach(function (entry) {
      const fc = entry && entry.forecast && entry.forecast.wind_probability_geojson;
      if (fc && Array.isArray(fc.features)) {
        fc.features.forEach(function (f) {
          all.push(f);
        });
      }
    });
    return all;
  }

  function showEmptyState(emptyEl, mapEl) {
    // Deliberately identical copy to Panel 1's off-season message — both
    // panels share the same "no active Atlantic system" ground truth, so
    // saying the same thing twice is clearer than inventing a second
    // phrasing that would read as a different condition.
    emptyEl.innerHTML =
      '<p class="text-sm">No active Atlantic storms — hurricane season runs June 1 – November 30.</p>';
    mapEl.classList.add("hidden");
  }

  function renderMap(mapEl, emptyEl, thresholdEl, features) {
    emptyEl.classList.add("hidden");
    mapEl.classList.remove("hidden");

    const map = L.map(mapEl, {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      scrollWheelZoom: false,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 10,
    }).addTo(map);

    // Pre-build one GeoJSON layer per threshold. Switching is then just
    // an add/remove toggle on the map — cheap, flicker-free, and means
    // the threshold dropdown reacts in <1 frame instead of waiting on a
    // re-parse.
    const layersByThreshold = {};
    SUPPORTED_THRESHOLDS.forEach(function (kt) {
      const kept = features.filter(function (f) {
        return (f.properties || {}).threshold_kt === kt;
      });
      layersByThreshold[kt] = L.geoJSON(
        { type: "FeatureCollection", features: kept },
        {
          style: styleForWspFeature,
          onEachFeature: function (feature, layer) {
            layer.bindPopup(buildFeaturePopup(feature.properties || {}, kt));
          },
        },
      );
    });

    let current = parseInt(thresholdEl.value, 10) || DEFAULT_THRESHOLD;
    if (!SUPPORTED_THRESHOLDS.includes(current)) {
      current = DEFAULT_THRESHOLD;
      thresholdEl.value = String(current);
    }
    layersByThreshold[current].addTo(map);

    // Fit once to the 34 kt envelope — it's always the widest of the
    // three layers (higher-threshold contours nest inside lower ones),
    // so switching thresholds later won't jerk the viewport around.
    try {
      const widestBounds = layersByThreshold[DEFAULT_THRESHOLD].getBounds();
      if (widestBounds.isValid()) {
        map.fitBounds(widestBounds, { padding: [20, 20] });
      }
    } catch (e) {
      // getBounds throws on empty layers; defensive fallback keeps the
      // default Atlantic view in that degenerate case.
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    }

    thresholdEl.addEventListener("change", function () {
      const next = parseInt(thresholdEl.value, 10);
      if (!SUPPORTED_THRESHOLDS.includes(next) || next === current) {
        return;
      }
      map.removeLayer(layersByThreshold[current]);
      layersByThreshold[next].addTo(map);
      current = next;
    });

    addLegend(map);
  }

  function styleForWspFeature(feature) {
    const p = pwindOf(feature);
    return {
      color: "#374151", // slate-700 stroke — quiet against any fill
      weight: 0.5,
      opacity: 0.7,
      fillColor: colorForProbability(p),
      fillOpacity: 0.55, // lets the coastline read through
    };
  }

  function pwindOf(feature) {
    const props = (feature && feature.properties) || {};
    // Accept upper-case (NHC DBF convention) and lower-case as a hedge
    // against a future NHC release changing the field case. The parser
    // preserves DBF case, so upper-case is the production path.
    const raw = props.PWIND;
    if (typeof raw === "number") return raw;
    const alt = props.pwind;
    if (typeof alt === "number") return alt;
    return 0;
  }

  function colorForProbability(p) {
    for (let i = 0; i < BANDS.length; i++) {
      if (p >= BANDS[i].min) {
        return BANDS[i].color;
      }
    }
    // Unreachable because BANDS ends at min=0, but defensive.
    return BANDS[BANDS.length - 1].color;
  }

  function buildFeaturePopup(props, thresholdKt) {
    const p = typeof props.PWIND === "number" ? props.PWIND : props.pwind;
    const parts = [];
    if (typeof p === "number") {
      parts.push(
        "<strong>" +
          p.toFixed(0) +
          "%</strong> chance of sustained winds ≥ " +
          thresholdKt +
          " kt",
      );
    } else {
      parts.push("Probability band, ≥ " + thresholdKt + " kt");
    }
    parts.push('<span style="color:#64748b;">5-day cumulative (NHC WSP)</span>');
    return parts.join("<br>");
  }

  function addLegend(map) {
    // Bottom-right so it doesn't collide with Leaflet's default zoom
    // controls (top-left) or the attribution strip (bottom-right is
    // Leaflet's default attribution slot, but Leaflet stacks controls
    // at the same corner vertically, so this reads cleanly above the
    // "© OpenStreetMap" line).
    const legend = L.control({ position: "bottomright" });
    legend.onAdd = function () {
      const div = L.DomUtil.create("div", "landfall-legend");
      // Inline styles — the panel is tiny enough that a separate CSS
      // rule would add surface area for minimal benefit, and the JS
      // file is already the only place that knows about BANDS.
      div.style.background = "rgba(255, 255, 255, 0.95)";
      div.style.padding = "6px 8px";
      div.style.border = "1px solid #e2e8f0";
      div.style.borderRadius = "4px";
      div.style.fontSize = "11px";
      div.style.lineHeight = "1.5";
      div.style.color = "#334155"; // slate-700

      const swatchRows = BANDS.map(function (band) {
        return (
          '<div style="display:flex;align-items:center;gap:6px;">' +
          '<span style="display:inline-block;width:14px;height:14px;' +
          "background:" +
          band.color +
          ";" +
          'border:0.5px solid #374151;"></span>' +
          "<span>" +
          band.label +
          "</span>" +
          "</div>"
        );
      }).join("");

      div.innerHTML =
        '<div style="font-weight:600;margin-bottom:4px;color:#0f172a;">Probability</div>' +
        swatchRows;
      return div;
    };
    legend.addTo(map);
  }
})();
