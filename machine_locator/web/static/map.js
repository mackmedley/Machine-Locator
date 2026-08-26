/* Placement map: scored prospects as coloured pins, click for the full
   scoring breakdown. */
(function () {
  var GRADE_COLORS = { "A+": "#1a9850", A: "#66bd63", B: "#d9b310", C: "#e08a2e", D: "#cf4b3f" };

  // Leaflet comes from a CDN. On a locked-down network it will not load, and
  // the prospect list is still the useful half of this page -- so treat the
  // map as optional rather than letting a missing global kill the whole view.
  var hasMap = typeof L !== "undefined";
  var map = null;
  var layer = null;

  if (hasMap) {
    map = L.map("map", { preferCanvas: true }).setView(window.MAP_CENTER, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  } else {
    document.getElementById("map").innerHTML =
      '<div class="map-fallback"><p><strong>Map unavailable.</strong></p>' +
      "<p>Leaflet could not be loaded from the CDN, so the map is switched off. " +
      "The ranked prospect list on the left still works, and " +
      "<code>mloc export sites -f geojson</code> gives you pins for Google My Maps.</p></div>";
  }

  var markers = {};
  var sites = [];

  var els = {
    minScore: document.getElementById("min-score"),
    minScoreOut: document.getElementById("min-score-out"),
    category: document.getElementById("category"),
    search: document.getElementById("search"),
    count: document.getElementById("result-count"),
    results: document.getElementById("results"),
    detail: document.getElementById("detail"),
    detailBody: document.getElementById("detail-body")
  };

  function money(n) { return n == null ? "-" : "$" + Number(n).toLocaleString(); }

  function render() {
    if (layer) layer.clearLayers();
    markers = {};
    els.results.innerHTML = "";

    sites.forEach(function (site) {
      if (layer) {
        var marker = L.circleMarker([site.lat, site.lon], {
          radius: 5 + site.score / 22,
          color: GRADE_COLORS[site.grade] || "#888",
          fillColor: GRADE_COLORS[site.grade] || "#888",
          fillOpacity: 0.75,
          weight: 1
        });
        marker.bindTooltip(site.score.toFixed(0) + " · " + site.name);
        marker.on("click", function () { showDetail(site); });
        marker.addTo(layer);
        markers[site.id] = marker;
      }

      var li = document.createElement("li");
      li.innerHTML =
        '<span class="badge g' + site.grade + '">' + site.score.toFixed(0) + "</span>" +
        '<span class="name">' + escapeHtml(site.name) + "</span>" +
        '<div class="meta">' + escapeHtml(site.category_label) +
        (site.address ? " · " + escapeHtml(site.address) : "") + "</div>";
      li.addEventListener("click", function () {
        if (map) {
          map.setView([site.lat, site.lon], 15);
          if (markers[site.id]) markers[site.id].openTooltip();
        }
        showDetail(site);
      });
      els.results.appendChild(li);
    });

    els.count.textContent = sites.length
      ? sites.length.toLocaleString() + " prospects shown"
      : "No sites match these filters.";
  }

  function bar(label, value) {
    var pct = Math.max(0, Math.min(100, (value / 10) * 100));
    return '<div class="bar-row"><span>' + label + '</span>' +
      '<span class="bar"><span style="width:' + pct + '%"></span></span>' +
      '<span class="num">' + value.toFixed(1) + "</span></div>";
  }

  function showDetail(site) {
    var bars = Object.keys(site.breakdown || {})
      .filter(function (k) { return k !== "saturation_penalty"; })
      .map(function (k) { return bar(k.replace(/_/g, " "), site.breakdown[k]); })
      .join("");
    var penalty = (site.breakdown || {}).saturation_penalty;

    els.detailBody.innerHTML =
      "<h2>" + escapeHtml(site.name) + "</h2>" +
      '<span class="badge g' + site.grade + '">' + site.score.toFixed(0) +
      "</span> <strong>" + escapeHtml(site.category_label) + "</strong>" +
      "<dl>" +
      row("Address", site.address || "not mapped") +
      row("Hours", site.opening_hours || "unknown") +
      (site.phone ? row("Phone", site.phone) : "") +
      (site.website ? row("Web", '<a href="' + escapeAttr(site.website) + '" target="_blank" rel="noopener">' + escapeHtml(site.website) + "</a>") : "") +
      row("Stock it with", site.sell_here || "-") +
      row("Competition", site.competitors_nearby + " nearby") +
      row("Machines on site", site.vending_nearby) +
      row("Map", '<a href="https://www.google.com/maps/search/?api=1&query=' +
        site.lat + "," + site.lon + '" target="_blank" rel="noopener">open in Google Maps</a>') +
      "</dl>" +
      '<div class="bars">' + bars +
      (penalty ? '<div class="bar-row"><span>saturation</span><span class="bar"><span style="width:' +
        (penalty / 18 * 100) + '%;background:#cf4b3f"></span></span><span class="num">-' +
        penalty.toFixed(0) + "</span></div>" : "") +
      "</div>" +
      "<ul>" + (site.reasons || []).map(function (r) {
        return "<li>" + escapeHtml(r) + "</li>";
      }).join("") + "</ul>";
    els.detail.hidden = false;
  }

  function row(label, value) { return "<dt>" + label + "</dt><dd>" + value + "</dd>"; }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function escapeAttr(s) { return escapeHtml(s).replace(/`/g, "&#96;"); }

  var timer = null;
  function load() {
    var params = new URLSearchParams({ min_score: els.minScore.value, limit: 3000 });
    if (els.category.value) params.set("category", els.category.value);
    if (els.search.value.trim()) params.set("search", els.search.value.trim());
    els.count.textContent = "Loading…";
    fetch("/api/sites?" + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) { sites = data.sites || []; render(); })
      .catch(function () { els.count.textContent = "Could not load sites."; });
  }
  function debounced() { clearTimeout(timer); timer = setTimeout(load, 250); }

  els.minScore.addEventListener("input", function () {
    els.minScoreOut.textContent = els.minScore.value;
    debounced();
  });
  els.category.addEventListener("change", load);
  els.search.addEventListener("input", debounced);
  document.getElementById("detail-close").addEventListener("click", function () {
    els.detail.hidden = true;
  });

  load();
})();
