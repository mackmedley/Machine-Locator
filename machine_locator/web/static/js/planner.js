/* Day planner: pick stops, order them into an efficient run, draw the line. */
(function () {
  "use strict";

  var map = null, layer = null;
  var hasMap = typeof L !== "undefined";

  if (hasMap) {
    map = L.map("map", { preferCanvas: true }).setView(window.MAP_CENTER, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  } else {
    document.getElementById("map").innerHTML =
      "<div class='map-fallback'><p><strong>Map unavailable</strong></p>" +
      "<p class='muted small'>The ordered stop list and the Google Maps link both still work.</p></div>";
  }

  var els = {
    top: document.getElementById("f-top"),
    score: document.getElementById("f-score"),
    scoreOut: document.getElementById("f-score-out"),
    stage: document.getElementById("f-stage"),
    start: document.getElementById("f-start"),
    stops: document.getElementById("stops"),
    stopCount: document.getElementById("stop-count"),
    distance: document.getElementById("distance"),
    summary: document.getElementById("summary"),
    maps: document.getElementById("btn-maps")
  };

  function render(data) {
    var stops = data.stops || [];
    if (layer) layer.clearLayers();

    if (!stops.length) {
      els.stops.innerHTML = "<div class='empty'><h3>No stops</h3><p>" +
        ML.esc(data.error || "Loosen the filters and plan again.") + "</p></div>";
      els.stopCount.textContent = "No route yet";
      els.distance.textContent = "";
      els.summary.textContent = "";
      els.maps.href = "#";
      return;
    }

    els.stopCount.textContent = stops.length + " stops";
    els.distance.textContent = data.distance_mi + " miles";
    els.summary.textContent = stops.length + " stops · " + data.distance_mi + " miles driving";
    els.maps.href = data.google_maps_url || "#";

    els.stops.innerHTML = stops.map(function (s) {
      return "<div class='result' data-lat='" + s.lat + "' data-lon='" + s.lon + "'>" +
        "<span class='grade " + ML.gradeClass(s.grade) + "'>" + s.order + "</span>" +
        "<div style='min-width:0;flex:1'>" +
          "<div class='name'>" + ML.esc(s.name) + "</div>" +
          "<div class='meta'>" + ML.esc(s.address || (s.lat.toFixed(4) + ", " + s.lon.toFixed(4))) + "</div>" +
          (s.phone ? "<div class='meta'><a href='tel:" + ML.esc(s.phone) + "'>" + ML.esc(s.phone) + "</a></div>" : "") +
        "</div></div>";
    }).join("");

    els.stops.querySelectorAll(".result").forEach(function (row) {
      row.onclick = function () {
        if (map) map.setView([Number(row.dataset.lat), Number(row.dataset.lon)], 16);
      };
    });

    if (layer) {
      var latlngs = stops.map(function (s) { return [s.lat, s.lon]; });
      if (data.start) latlngs.unshift(data.start);
      L.polyline(latlngs, { color: "#2a78d6", weight: 3, opacity: .75 }).addTo(layer);
      stops.forEach(function (s) {
        L.marker([s.lat, s.lon]).addTo(layer)
          .bindTooltip(s.order + ". " + s.name, { permanent: false });
      });
      map.fitBounds(L.latLngBounds(latlngs).pad(0.15));
    }
  }

  function plan() {
    var params = new URLSearchParams({
      top: els.top.value, min_score: els.score.value
    });
    if (els.stage.value) params.set("stage", els.stage.value);
    if (els.start.value.trim()) params.set("start", els.start.value.trim());
    els.summary.textContent = "Planning…";
    ML.api("/api/planner/route?" + params).then(render)
      .catch(function (e) { ML.toast(e.message, "bad"); els.summary.textContent = ""; });
  }

  els.score.oninput = function () { els.scoreOut.textContent = els.score.value; };
  document.getElementById("btn-plan").onclick = plan;
  plan();
})();
