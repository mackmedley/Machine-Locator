/* Prospect browser: filters, synced map + list, multi-select, detail drawer. */
(function () {
  "use strict";

  var GRADE_COLORS = {
    "A+": "--grade-ap", "A": "--grade-a", "B": "--grade-b", "C": "--grade-c", "D": "--grade-d"
  };
  function gradeColor(grade) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(GRADE_COLORS[grade] || "--grade-d").trim() || "#2a78d6";
  }

  var sites = [];
  var selected = new Set();
  var markers = {};
  var map = null, layer = null;
  var hasMap = typeof L !== "undefined";

  var els = {
    score: document.getElementById("f-score"),
    scoreOut: document.getElementById("f-score-out"),
    category: document.getElementById("f-category"),
    grade: document.getElementById("f-grade"),
    search: document.getElementById("f-search"),
    count: document.getElementById("result-count"),
    results: document.getElementById("results"),
    selAll: document.getElementById("sel-all"),
    selBar: document.getElementById("selection-bar"),
    selCount: document.getElementById("sel-count")
  };

  if (hasMap) {
    map = L.map("map", { preferCanvas: true, zoomControl: true }).setView(window.MAP_CENTER, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  } else {
    document.getElementById("map").innerHTML =
      '<div class="map-fallback"><p><strong>Map unavailable</strong></p>' +
      "<p class='muted small'>The map library couldn't load from the internet. The ranked list " +
      "still works, and <em>Map file</em> gives you pins for Google My Maps.</p></div>";
  }

  function render() {
    if (layer) layer.clearLayers();
    markers = {};
    els.results.innerHTML = "";

    if (!sites.length) {
      els.results.innerHTML =
        '<div class="empty"><h3>Nothing matches</h3><p>Loosen the filters, or run ' +
        "<strong>Find prospects</strong> to scan for more.</p></div>";
      els.count.textContent = "0 prospects";
      return;
    }

    var frag = document.createDocumentFragment();
    sites.forEach(function (site) {
      if (layer) {
        var marker = L.circleMarker([site.lat, site.lon], {
          radius: 4.5 + site.score / 24,
          color: gradeColor(site.grade), fillColor: gradeColor(site.grade),
          fillOpacity: .78, weight: 1.2
        });
        marker.bindTooltip(Math.round(site.score) + " · " + site.name);
        marker.on("click", function () { openDetail(site.id); });
        marker.addTo(layer);
        markers[site.id] = marker;
      }

      var row = document.createElement("div");
      row.className = "result" + (selected.has(site.id) ? " selected" : "");
      row.innerHTML =
        '<input type="checkbox" ' + (selected.has(site.id) ? "checked" : "") + ' aria-label="Select">' +
        ML.gradeBadge(site) +
        "<div style='min-width:0;flex:1'>" +
          "<div class='name'>" + ML.esc(site.name) + "</div>" +
          "<div class='meta'>" + ML.esc(site.category_label) +
          (site.address ? " · " + ML.esc(site.address) : "") + "</div>" +
        "</div>";

      row.querySelector("input").onclick = function (e) {
        e.stopPropagation();
        toggle(site.id, e.target.checked);
        row.classList.toggle("selected", e.target.checked);
      };
      row.onclick = function () {
        if (map) map.setView([site.lat, site.lon], 15);
        openDetail(site.id);
      };
      frag.appendChild(row);
    });
    els.results.appendChild(frag);
    els.count.textContent = sites.length.toLocaleString() + " prospects";
  }

  function toggle(id, on) {
    if (on) selected.add(id); else selected.delete(id);
    els.selCount.textContent = selected.size;
    els.selBar.hidden = selected.size === 0;
  }

  // Honour ?category= so the dashboard's site-type table can deep-link here.
  (function applyQueryString() {
    var incoming = new URLSearchParams(location.search).get("category");
    if (incoming && els.category.querySelector('option[value="' + incoming + '"]')) {
      els.category.value = incoming;
    }
  })();

  function load() {
    var params = new URLSearchParams({ min_score: els.score.value, limit: 3000 });
    if (els.category.value) params.set("category", els.category.value);
    if (els.grade.value) params.set("grade", els.grade.value);
    if (els.search.value.trim()) params.set("search", els.search.value.trim());
    els.count.textContent = "Loading…";
    ML.api("/api/sites?" + params).then(function (data) {
      sites = data.sites || [];
      render();
    }).catch(function (e) { ML.toast(e.message, "bad"); });
  }

  /* ------------------------------------------------------------- detail */

  function openDetail(siteId) {
    ML.api("/api/sites/" + encodeURIComponent(siteId)).then(function (data) {
      var s = data.site, p = data.pipeline || {};
      var bars = Object.keys(s.breakdown || {})
        .filter(function (k) { return k !== "saturation_penalty"; })
        .map(function (k) {
          var v = s.breakdown[k];
          return "<div style='display:grid;grid-template-columns:104px 1fr 32px;gap:8px;align-items:center;margin-bottom:5px;font-size:12px'>" +
            "<span class='muted'>" + ML.esc(k.replace(/_/g, " ")) + "</span>" +
            "<span class='progress'><span style='width:" + (v / 10 * 100) + "%'></span></span>" +
            "<span class='num'>" + v.toFixed(1) + "</span></div>";
        }).join("");

      var stageOptions = ["new", "queued", "contacted", "following_up", "interested", "won", "lost"]
        .map(function (k) {
          var label = k.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
          return "<option value='" + k + "'" + (p.stage === k ? " selected" : "") + ">" + label + "</option>";
        }).join("");

      var panel = ML.drawer.open(
        "<div class='drawer-head'>" +
          "<div style='min-width:0;flex:1'>" +
            "<div style='display:flex;align-items:center;gap:9px;margin-bottom:3px'>" +
              ML.gradeBadge(s) + "<span class='badge'>" + ML.esc(s.category_label) + "</span>" +
              (data.suppressed ? "<span class='status status-critical'>do not contact</span>" : "") +
            "</div>" +
            "<h2>" + ML.esc(s.name) + "</h2>" +
            "<div class='muted small'>" + ML.esc(s.address || "No address on file") + "</div>" +
          "</div>" +
          "<button class='icon-btn' data-close aria-label='Close'>&times;</button>" +
        "</div>" +
        "<div class='drawer-body'>" +
          "<dl class='kv'>" +
            "<dt>Stock it with</dt><dd>" + ML.esc(s.sell_here || "—") + "</dd>" +
            "<dt>Hours</dt><dd>" + ML.esc(s.opening_hours || "unknown") + "</dd>" +
            "<dt>Competition</dt><dd>" + s.competitors_nearby + " within 400m</dd>" +
            "<dt>Machines on site</dt><dd>" + s.vending_nearby + "</dd>" +
            (s.phone ? "<dt>Phone</dt><dd><a href='tel:" + ML.esc(s.phone) + "'>" + ML.esc(s.phone) + "</a></dd>" : "") +
            (s.website ? "<dt>Web</dt><dd><a href='" + ML.esc(ML.safeUrl(s.website)) + "' target='_blank' rel='noopener'>site</a></dd>" : "") +
            "<dt>Map</dt><dd><a target='_blank' rel='noopener' href='https://www.google.com/maps/search/?api=1&query=" +
              s.lat + "," + s.lon + "'>Open in Google Maps</a></dd>" +
          "</dl>" +

          "<h3 style='margin:16px 0 8px'>Why it scores " + Math.round(s.score) + "</h3>" + bars +
          "<ul style='margin:10px 0 0;padding-left:18px;color:var(--text-2);font-size:12.5px'>" +
            (s.reasons || []).map(function (r) { return "<li>" + ML.esc(r) + "</li>"; }).join("") +
          "</ul>" +

          "<h3 style='margin:20px 0 8px'>Contact &amp; stage</h3>" +
          "<div class='grid' style='grid-template-columns:1fr 1fr;gap:10px'>" +
            "<label class='field'><span class='lbl'>Stage</span><select id='d-stage'>" + stageOptions + "</select></label>" +
            "<label class='field'><span class='lbl'>Contact name</span><input id='d-name' type='text' value='" + ML.esc(p.contact_name || "") + "'></label>" +
          "</div>" +
          "<label class='field'><span class='lbl'>Contact email</span>" +
            "<input id='d-email' type='email' value='" + ML.esc(p.contact_email || s.email || "") + "' placeholder='manager@business.com'>" +
            "<span class='help'>Needed to queue outreach. OpenStreetMap rarely has one — check their website.</span></label>" +

          "<h3 style='margin:20px 0 8px'>Add a note</h3>" +
          "<textarea id='d-note' placeholder='Spoke to the owner — call back Thursday'></textarea>" +
          "<button class='btn btn-sm' id='d-save-note' style='margin-top:8px'>Save note</button>" +

          "<h3 style='margin:22px 0 8px'>History</h3>" +
          (data.activities.length
            ? "<ul class='timeline'>" + data.activities.map(function (a) {
                return "<li><span class='dot-mark'></span><div style='min-width:0'>" +
                  "<div class='what'>" + ML.esc(a.title || a.kind) + "</div>" +
                  (a.body ? "<div class='detail'>" + ML.esc(String(a.body).slice(0, 400)) + "</div>" : "") +
                  "<div class='when'>" + ML.when(a.created_at) + "</div></div></li>";
              }).join("") + "</ul>"
            : "<p class='muted small'>Nothing logged yet.</p>") +
        "</div>" +
        "<div class='drawer-foot'>" +
          "<button class='btn btn-primary' id='d-save'>Save</button>" +
          "<button class='btn' id='d-outreach'>Start outreach</button>" +
          (s.phone ? "<a class='btn' href='tel:" + ML.esc(s.phone) + "'>Call</a>" : "") +
          "<div style='flex:1'></div>" +
          "<button class='btn btn-danger' id='d-optout'>Never contact</button>" +
        "</div>"
      );

      function collect() {
        return {
          stage: panel.querySelector("#d-stage").value,
          contact_name: panel.querySelector("#d-name").value,
          contact_email: panel.querySelector("#d-email").value
        };
      }

      panel.querySelector("#d-save").onclick = function () {
        ML.api("/api/sites/" + encodeURIComponent(siteId) + "/pipeline", { body: collect() })
          .then(function () { ML.toast("Saved", "good"); ML.drawer.close(); load(); })
          .catch(function (e) { ML.toast(e.message, "bad"); });
      };
      panel.querySelector("#d-save-note").onclick = function () {
        var note = panel.querySelector("#d-note").value.trim();
        if (!note) return;
        ML.api("/api/sites/" + encodeURIComponent(siteId) + "/note", { body: { note: note } })
          .then(function () { ML.toast("Note saved", "good"); openDetail(siteId); })
          .catch(function (e) { ML.toast(e.message, "bad"); });
      };
      panel.querySelector("#d-outreach").onclick = function () {
        ML.api("/api/sites/" + encodeURIComponent(siteId) + "/pipeline", { body: collect() })
          .then(function () { ML.drawer.close(); startOutreach([siteId]); });
      };
      panel.querySelector("#d-optout").onclick = function () {
        ML.api("/api/sites/" + encodeURIComponent(siteId) + "/reply",
          { body: { text: "Marked do-not-contact", opted_out: true } })
          .then(function () { ML.toast("Added to your do-not-contact list", "good"); ML.drawer.close(); load(); })
          .catch(function (e) { ML.toast(e.message, "bad"); });
      };
    }).catch(function (e) { ML.toast(e.message, "bad"); });
  }

  /* ----------------------------------------------------------- outreach */

  function startOutreach(ids) {
    ML.api("/api/outreach/preview", { body: { site_ids: ids } }).then(function (data) {
      var ready = data.ready || [], blocked = data.blocked || [];
      var first = ready[0];

      var gateHtml = "";
      if (!data.gate.allowed) {
        gateHtml = "<div class='banner banner-warn'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9'><path d='M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z'/></svg><div class='spacer'>" +
          data.gate.reasons.map(ML.esc).join("<br>") +
          " <a href='/settings'>Open settings</a></div></div>";
      }

      var panel = ML.drawer.open(
        "<div class='card'>" +
          "<div class='card-head'><h2>Start outreach</h2><div class='spacer'></div>" +
            "<button class='icon-btn' data-close>&times;</button></div>" +
          "<div class='card-body'>" +
            gateHtml +
            "<p><strong>" + ready.length + "</strong> prospect(s) will each be queued a " +
              data.steps + "-message sequence" +
              (blocked.length ? ", and <strong>" + blocked.length + "</strong> will be skipped" : "") + ".</p>" +
            (blocked.length
              ? "<details style='margin-bottom:12px'><summary class='muted small' style='cursor:pointer'>Why " +
                blocked.length + " skipped</summary><ul class='small muted' style='padding-left:18px;margin-top:6px'>" +
                blocked.slice(0, 40).map(function (b) {
                  return "<li>" + ML.esc(b.name) + " — " + ML.esc(b.problem) + "</li>";
                }).join("") + "</ul></details>"
              : "") +
            (first
              ? "<h3 style='margin:14px 0 8px'>Preview for " + ML.esc(first.name) + "</h3>" +
                first.steps.map(function (step) {
                  return "<div class='preview-mail'>" +
                    "<div class='when'>" + ML.esc(step.name) +
                      (step.delay_days ? " · sends in " + step.delay_days + " days" : " · sends immediately") + "</div>" +
                    "<div class='subject'>" + ML.esc(step.subject) + "</div>" +
                    "<div class='body'>" + ML.esc(step.body) + "</div></div>";
                }).join("") +
                "<p class='muted small'>A compliance footer with your postal address and an opt-out is added to every message when it sends.</p>"
              : "<p class='muted'>Nothing to preview — none of the selected prospects can be emailed yet. " +
                "Add a contact email on each one first.</p>") +
          "</div>" +
          "<div class='drawer-foot'>" +
            "<button class='btn btn-primary' id='o-confirm'" + (ready.length ? "" : " disabled") + ">" +
              "Queue " + ready.length + " sequence(s)</button>" +
            "<button class='btn' data-close>Cancel</button>" +
          "</div>" +
        "</div>", { centered: true });

      var confirm = panel.querySelector("#o-confirm");
      if (confirm) confirm.onclick = function () {
        confirm.disabled = true;
        ML.api("/api/outreach/enroll", { body: { site_ids: ready.map(function (r) { return r.site_id; }) } })
          .then(function (res) {
            ML.drawer.close();
            ML.toast(res.enrolled + " prospect(s) queued, " + res.messages + " emails scheduled", "good");
            selected.clear(); toggle(null, false); load();
          })
          .catch(function (e) { ML.toast(e.message, "bad"); confirm.disabled = false; });
      };
    }).catch(function (e) { ML.toast(e.message, "bad"); });
  }

  /* ------------------------------------------------------------- wiring */

  els.score.oninput = function () {
    els.scoreOut.textContent = els.score.value;
    debounced();
  };
  els.category.onchange = load;
  els.grade.onchange = load;
  els.search.oninput = ML.debounce(load, 260);
  var debounced = ML.debounce(load, 200);

  els.selAll.onchange = function () {
    sites.forEach(function (s) {
      if (els.selAll.checked) selected.add(s.id); else selected.delete(s.id);
    });
    toggle(null, false);
    render();
  };
  document.getElementById("btn-clear-sel").onclick = function () {
    selected.clear(); els.selAll.checked = false; toggle(null, false); render();
  };
  document.getElementById("btn-outreach-sel").onclick = function () {
    startOutreach(Array.from(selected));
  };
  document.getElementById("btn-scan").onclick = function () {
    ML.jobs.start("/api/jobs/scan", { territories: 4 }, load);
  };

  load();
})();
