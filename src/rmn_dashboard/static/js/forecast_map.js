/* Panel 1 — Active storms cone map.
 *
 * Fetches /api/v1/forecasts/active, renders the cone polygon and 5-day
 * track points for each active storm on a Leaflet map, and falls back to
 * an empty-state message during the off-season (and, separately, when
 * the fetch itself fails — those two states look different to the user).
 *
 * Why client-rendered: the server ships an empty #forecast-map <div> in
 * the template and this script takes over on DOMContentLoaded. That
 * keeps the Jinja template stable across season/off-season (no branchy
 * server-side maps) and means a mid-session ingest tick that adds a
 * storm to the DB shows up on the next poll without a page reload.
 *
 * Intentionally no framework — small surface, Leaflet is already on the
 * page via base.html, and we want this panel to render in one repaint
 * rather than waiting on a JS bundle.
 */

(function () {
  "use strict";

  // Atlantic basin centroid + a zoom that shows Gulf of Mexico + Leeward
  // Islands in one frame. Actual bounds get refitted once we have a cone
  // to size against; this is just the initial paint so the empty ocean
  // doesn't flash.
  const DEFAULT_CENTER = [25.0, -70.0];
  const DEFAULT_ZOOM = 4;

  // NHC cone is conventionally drawn in a warning orange. Semi-transparent
  // so the coastline underneath stays readable — the whole point of the
  // panel is to see which land the cone touches.
  const CONE_STYLE = {
    color: "#ea580c", // tailwind orange-600 — matches the dashboard accent palette
    weight: 1,
    opacity: 0.8,
    fillColor: "#fb923c", // tailwind orange-400
    fillOpacity: 0.25,
  };

  // Small circle markers for the 5-day forecast points. Sized so 5 of
  // them along a track don't overlap at the default zoom.
  const FORECAST_POINT_STYLE = {
    radius: 5,
    color: "#9a3412", // orange-800
    weight: 1,
    fillColor: "#fdba74", // orange-300
    fillOpacity: 0.9,
  };

  // Current-position marker is deliberately larger + a different colour
  // so users can pick out "where the storm is right now" vs "where NHC
  // thinks it's going" at a glance.
  const CURRENT_POSITION_STYLE = {
    radius: 8,
    color: "#7f1d1d", // red-900
    weight: 2,
    fillColor: "#dc2626", // red-600
    fillOpacity: 0.95,
  };

  document.addEventListener("DOMContentLoaded", function () {
    const mapEl = document.getElementById("forecast-map");
    const emptyEl = document.getElementById("forecast-map-empty");
    const advisoryEl = document.getElementById("forecast-map-advisory");
    const detailsEl = document.getElementById("forecast-storm-details");

    // The map div only exists on the dashboard index. Script is loaded
    // from base.html on every page in principle, but we scope early.
    if (!mapEl || !emptyEl) {
      return;
    }

    fetch("/api/v1/forecasts/active", { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        const storms = (payload && payload.storms) || [];
        // Populate the details block unconditionally — it handles the
        // empty case by clearing itself, so an off-season page has
        // blank space (matching the empty-state copy tone) rather than
        // a stale readout from a previous poll.
        populateStormDetails(detailsEl, storms);
        if (storms.length === 0) {
          showEmptyState(emptyEl, mapEl);
          return;
        }
        renderMap(mapEl, emptyEl, advisoryEl, storms);
      })
      .catch(function (err) {
        // On fetch failure show a distinct message — a silent empty
        // state would hide a broken endpoint during incidents. Also
        // clear the details block so a stale readout doesn't sit there
        // while the error banner claims the feed is broken.
        console.error("forecast-map fetch failed", err);
        populateStormDetails(detailsEl, []);
        emptyEl.innerHTML =
          '<p class="text-sm text-rose-500">Forecast feed unavailable — try refreshing.</p>';
        mapEl.classList.add("hidden");
      });
  });

  function showEmptyState(emptyEl, mapEl) {
    // Same message year-round during the off-season. Atlantic hurricane
    // season officially runs Jun 1 – Nov 30; NHC occasionally issues
    // pre-season products for May systems but those still show up via
    // /api/v1/forecasts/active when they exist.
    emptyEl.innerHTML =
      '<p class="text-sm">No active Atlantic storms — hurricane season runs June 1 – November 30.</p>';
    mapEl.classList.add("hidden");
  }

  function renderMap(mapEl, emptyEl, advisoryEl, storms) {
    // Reveal the map container first so Leaflet can measure it. Leaflet
    // silently refuses to size a hidden div.
    emptyEl.classList.add("hidden");
    mapEl.classList.remove("hidden");

    const map = L.map(mapEl, {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      scrollWheelZoom: false, // page-scroll-friendly; users click to interact
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 10,
    }).addTo(map);

    const allLayers = [];

    storms.forEach(function (entry) {
      const storm = entry.storm || {};
      const forecast = entry.forecast || {};
      const position = entry.current_position;

      const label =
        (storm.storm_type || "Storm") + " " + (storm.name || storm.nhc_id || "—");

      // Cone polygon
      if (forecast.cone_geojson) {
        const coneLayer = L.geoJSON(forecast.cone_geojson, {
          style: CONE_STYLE,
        })
          .bindPopup(label + " — 5-day cone")
          .addTo(map);
        allLayers.push(coneLayer);
      }

      // 5-day track points.
      //
      // Production shape (NHC _5day_pts parsed via
      // rmn_dashboard.scrapers.nhc_shapefiles): a list of GeoJSON
      // Features, each a Point geometry with properties carrying the
      // DBF fields verbatim (ADVISNUM, LAT, LON, MAXWIND, FLDATELBL,
      // TCDVLP, ...). Render via L.geoJSON with a pointToLayer callback
      // so we keep the circle-marker styling rather than Leaflet's
      // default pin icon.
      const points = forecast.forecast_5day_points || [];
      if (points.length > 0) {
        const pointsLayer = L.geoJSON(
          { type: "FeatureCollection", features: points },
          {
            pointToLayer: function (feature, latlng) {
              return L.circleMarker(latlng, FORECAST_POINT_STYLE);
            },
            onEachFeature: function (feature, layer) {
              layer.bindPopup(buildPointPopup(label, feature.properties || {}));
            },
          },
        ).addTo(map);
        allLayers.push(pointsLayer);
      }

      // Current-position marker (larger, different colour)
      if (
        position &&
        typeof position.latitude_deg === "number" &&
        typeof position.longitude_deg === "number"
      ) {
        const marker = L.circleMarker(
          [position.latitude_deg, position.longitude_deg],
          CURRENT_POSITION_STYLE,
        )
          .bindPopup(buildCurrentPopup(label, position))
          .addTo(map);
        allLayers.push(marker);
      }
    });

    // Fit bounds to whatever we drew, padded so markers on the edge
    // aren't clipped by the panel border.
    if (allLayers.length > 0) {
      const group = L.featureGroup(allLayers);
      try {
        map.fitBounds(group.getBounds(), { padding: [20, 20] });
      } catch (e) {
        // getBounds throws on an empty group; defensive fallback keeps
        // the default Atlantic view.
        map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
      }
    }

    // Advisory timestamp — small header annotation showing how fresh
    // the product is. Uses the latest storm in the list as the proxy
    // because the UI currently only shows "the active basin", not one
    // storm at a time.
    if (advisoryEl && storms[0] && storms[0].forecast && storms[0].forecast.issued_at) {
      advisoryEl.textContent = "Issued " + storms[0].forecast.issued_at;
    }
  }

  function buildPointPopup(stormLabel, props) {
    // Read from DBF properties — NHC's `_5day_pts` shapefile publishes
    // FLDATELBL (human-readable valid-time label), MAXWIND (kt), and
    // TCDVLP (development stage code, e.g. "H" / "S" / "D"). Field
    // names are upper-case per the DBF schema; we fall back to lower-
    // case variants just in case a future NHC release changes that.
    const validLabel = props.FLDATELBL || props.fldatelbl || props.valid_at;
    const maxWind = pickNumber(props.MAXWIND, props.maxwind, props.intensity_kt);
    const devStage = props.TCDVLP || props.tcdvlp || props.classification;

    const parts = [stormLabel];
    if (validLabel) parts.push("Valid: " + validLabel);
    if (maxWind !== null) parts.push(maxWind + " kt");
    if (devStage) parts.push(devStage);
    return parts.join("<br>");
  }

  function pickNumber(/* ...candidates */) {
    for (let i = 0; i < arguments.length; i++) {
      if (typeof arguments[i] === "number") return arguments[i];
    }
    return null;
  }

  function buildCurrentPopup(stormLabel, position) {
    const parts = [stormLabel + " — current position"];
    if (position.observation_time) parts.push("At: " + position.observation_time);
    if (typeof position.intensity_kt === "number")
      parts.push(position.intensity_kt + " kt");
    if (typeof position.pressure_mb === "number")
      parts.push(position.pressure_mb + " mb");
    if (position.classification) parts.push(position.classification);
    return parts.join("<br>");
  }

  // --- Per-storm details readout (below the map) -----------------------

  // Translates the three "storm is doing X right now" facts that live
  // in entry.storm + entry.current_position into a one-line readout.
  // The map already shows *where* the storm is; this block answers
  // *what* it is (category), *how strong* (winds, pressure), and
  // *which way it's heading*. Same data as the current-position popup,
  // just always visible instead of requiring a click.

  function populateStormDetails(detailsEl, storms) {
    // Defensive: if the details element isn't in the DOM (e.g. a
    // future page that loads this script but skips the readout), no-op
    // rather than crash.
    if (!detailsEl) {
      return;
    }
    if (!storms || storms.length === 0) {
      detailsEl.innerHTML = "";
      return;
    }
    // Storms arrive pre-sorted by nhc_id from the service layer; we
    // keep that order so the readout matches whatever the map and the
    // advisory-timestamp label are reflecting.
    detailsEl.innerHTML = storms.map(buildStormDetailRow).join("");
  }

  function buildStormDetailRow(entry) {
    const storm = (entry && entry.storm) || {};
    const pos = entry && entry.current_position; // may be null
    const parts = [];

    const name = storm.name || storm.nhc_id || "Unnamed";
    parts.push('<strong class="text-slate-900">' + escapeHtml(name) + "</strong>");

    const category = categoryLabel(pos, storm);
    if (category) {
      parts.push('<span class="text-slate-600">' + escapeHtml(category) + "</span>");
    }

    if (pos && typeof pos.intensity_kt === "number") {
      parts.push(
        '<span class="font-mono text-slate-700">' + pos.intensity_kt + " kt</span>",
      );
    }
    if (pos && typeof pos.pressure_mb === "number") {
      parts.push(
        '<span class="font-mono text-slate-700">' + pos.pressure_mb + " mb</span>",
      );
    }

    const movement = movementLabel(pos);
    if (movement) {
      parts.push('<span class="text-slate-600">' + escapeHtml(movement) + "</span>");
    }

    // Row styling: small vertical padding, dot separators via spans so
    // they stay colour-independent and won't get picked up by
    // selection/copy as part of the names. divide-y on the parent
    // draws the inter-row rule.
    return (
      '<div class="py-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">' +
      parts.join('<span class="text-slate-300">·</span>') +
      "</div>"
    );
  }

  function categoryLabel(pos, storm) {
    // Prefer Saffir-Simpson category when winds are hurricane-force,
    // because "Cat 3" is how underwriters and newsrooms refer to
    // storms. Below hurricane force fall back to the storm_type string
    // ("Tropical Storm", "Tropical Depression") so users don't see a
    // naked classification code like "TS".
    const stype = storm.storm_type || "";
    const kt = pos && typeof pos.intensity_kt === "number" ? pos.intensity_kt : null;

    if (kt !== null) {
      // Saffir-Simpson thresholds (kt), per NHC:
      //   Cat 5 ≥ 137, Cat 4 ≥ 113, Cat 3 ≥ 96, Cat 2 ≥ 83, Cat 1 ≥ 64
      let cat = null;
      if (kt >= 137) cat = "Cat 5";
      else if (kt >= 113) cat = "Cat 4";
      else if (kt >= 96) cat = "Cat 3";
      else if (kt >= 83) cat = "Cat 2";
      else if (kt >= 64) cat = "Cat 1";
      if (cat) {
        return stype ? stype + " · " + cat : cat;
      }
    }
    return stype || null;
  }

  function movementLabel(pos) {
    if (!pos) return null;
    const dir = pos.movement_dir_deg;
    const speed = pos.movement_speed_mph;
    const haveDir = typeof dir === "number";
    const haveSpeed = typeof speed === "number";
    if (!haveDir && !haveSpeed) return null;
    if (haveDir && haveSpeed) {
      return "Moving " + degreesToCardinal(dir) + " at " + speed + " mph";
    }
    if (haveDir) return "Moving " + degreesToCardinal(dir);
    return "Moving at " + speed + " mph";
  }

  // 16-point compass rose → matches the resolution NHC publishes in
  // its advisory text products (e.g. "NORTH-NORTHEAST OR 015 DEGREES").
  // Finer than 16-point would read as false precision given NHC
  // rounds its own movement headings to 5 degrees.
  const CARDINAL_16 = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
  ];

  function degreesToCardinal(deg) {
    // Normalize to [0, 360), then bucket into 22.5° slices centered on
    // each cardinal.
    const normalized = ((deg % 360) + 360) % 360;
    const idx = Math.round(normalized / 22.5) % 16;
    return CARDINAL_16[idx];
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
