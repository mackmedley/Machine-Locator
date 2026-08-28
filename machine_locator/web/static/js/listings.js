/* Routes-for-sale browser: filters, deal maths, and an empty state that
   explains itself. */
(function () {
  "use strict";

  var els = {};
  ["rows", "empty-state", "count", "f-q", "f-rel", "f-rel-out", "f-local", "f-fin",
   "more-filters", "btn-more", "btn-clear", "f-pmin", "f-pmax", "f-mach",
   "f-source", "f-days", "source-card", "source-rows"].forEach(function (id) {
    els[id] = document.getElementById(id);
  });

  var lastFacets = null;

  function money(n) {
    if (n == null || n === "") return "—";
    n = Number(n);
    if (!isFinite(n)) return "—";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
    if (n >= 1e4) return "$" + Math.round(n / 1e3) + "K";
    return "$" + Math.round(n).toLocaleString();
  }

  function filtersAreOn() {
    return Number(els["f-rel"].value) > 0 || els["f-local"].checked ||
      els["f-fin"].checked || els["f-q"].value.trim() ||
      els["f-pmin"].value || els["f-pmax"].value || els["f-mach"].value ||
      els["f-source"].value || els["f-days"].value;
  }

  function clearFilters() {
    els["f-rel"].value = 0; els["f-rel-out"].textContent = "0";
    els["f-local"].checked = false;
    els["f-fin"].checked = false;
    els["f-q"].value = "";
    ["f-pmin", "f-pmax", "f-mach"].forEach(function (k) { els[k].value = ""; });
    els["f-source"].value = "";
    els["f-days"].value = "";
    load();
  }

  /* The two empty states need opposite responses from the user, so they must
     never look the same: filters hiding everything is "loosen this", nothing
     stored is "go and fetch some". */
  function renderEmpty(facets) {
    var total = (facets && facets.total) || 0;
    if (total > 0) {
      els["empty-state"].innerHTML =
        "<div class='empty'><h3>Nothing matches your filters</h3>" +
        "<p>You have <strong>" + total + "</strong> listing" + (total === 1 ? "" : "s") +
        " stored" + (facets.local ? ", " + facets.local + " of them in the OKC metro" : "") +
        ". The filters above are hiding them.</p>" +
        "<button class='btn btn-primary' id='empty-clear'>Show everything</button></div>";
      var btn = document.getElementById("empty-clear");
      if (btn) btn.onclick = clearFilters;
      return;
    }
    els["empty-state"].innerHTML =
      "<div class='empty'><h3>No routes stored yet</h3>" +
      "<p><strong>Check marketplaces</strong> looks for vending routes on sale. " +
      "Several of the big sites block automated access, so if it comes back thin, " +
      "set up their free email alerts and bring the results in with " +
      "<strong>Import CSV</strong> — imported listings are scored the same way.</p>" +
      "<div class='btn-group' style='justify-content:center'>" +
        "<button class='btn btn-primary' id='empty-find'>Check marketplaces</button>" +
        "<button class='btn' id='empty-import'>Import CSV</button></div></div>";
    var f = document.getElementById("empty-find");
    var i = document.getElementById("empty-import");
    if (f) f.onclick = function () { document.getElementById("btn-find").click(); };
    if (i) i.onclick = function () { document.getElementById("btn-import").click(); };
  }

  function renderSources(status) {
    if (!status || !status.length) { els["source-card"].hidden = true; return; }
    els["source-card"].hidden = false;
    var LABEL = { ok: "status-good", skipped: "status-warning", error: "status-critical" };
    var WORD = { ok: "worked", skipped: "blocked us", error: "failed" };
    els["source-rows"].innerHTML = status.map(function (s) {
      return "<tr><td>" + ML.esc(s.label || s.name) + "</td>" +
        "<td><span class='status " + (LABEL[s.status] || "status-muted") + "'>" +
          ML.esc(WORD[s.status] || s.status) + "</span></td>" +
        "<td class='num'>" + (s.found || 0) + "</td>" +
        "<td class='small muted'>" + ML.esc(s.why || (s.fallback ? "read with the backup reader" : "")) + "</td></tr>";
    }).join("");
  }

  function fillSources(facets) {
    if (!facets || !facets.sources) return;
    var current = els["f-source"].value;
    els["f-source"].innerHTML = "<option value=''>Anywhere</option>" +
      facets.sources.map(function (s) {
        return "<option value='" + ML.esc(s.source) + "'>" +
          ML.esc(s.source) + " (" + s.n + ")</option>";
      }).join("");
    els["f-source"].value = current;
  }

  function render(data) {
    var listings = data.listings || [];
    lastFacets = data.facets;
    fillSources(data.facets);
    renderSources(data.sources_status);

    var total = (data.facets && data.facets.total) || 0;
    els["count"].textContent = listings.length + " of " + total + " shown";
    els["empty-state"].innerHTML = "";

    if (!listings.length) {
      els["rows"].innerHTML = "";
      renderEmpty(data.facets);
      return;
    }

    els["rows"].innerHTML = listings.map(function (l) {
      var per = (l.price && l.machine_count) ? money(l.price / l.machine_count) : "—";
      var mult = (l.price && l.cash_flow) ? (l.price / l.cash_flow).toFixed(1) + "×" : "—";
      var href = ML.safeUrl(l.url);
      var title = href
        ? "<a href='" + ML.esc(href) + "' target='_blank' rel='noopener'>" + ML.esc(l.title) + "</a>"
        : ML.esc(l.title);
      return "<tr>" +
        "<td class='num'><strong>" + Math.round(l.relevance) + "</strong></td>" +
        "<td style='max-width:420px'>" + title +
          (l.is_local ? " <span class='badge' style='background:var(--good-soft);color:var(--good);border-color:transparent'>metro</span>" : "") +
          "<div class='why'>" + ML.esc((l.relevance_reasons || []).join(" · ")) + "</div></td>" +
        "<td class='num'>" + money(l.price) + "</td>" +
        "<td class='num'>" + money(l.cash_flow) + "</td>" +
        "<td class='num'>" + (l.machine_count || "—") + "</td>" +
        "<td class='num'>" + per + "</td>" +
        "<td class='num'>" + mult + "</td>" +
        "<td class='small'>" + ML.esc(l.location_text || "—") + "</td>" +
        "<td class='small'>" + ML.esc(l.source) + "</td>" +
        "<td class='small nowrap'>" + ML.esc((l.first_seen || "").slice(0, 10)) + "</td>" +
      "</tr>";
    }).join("");
  }

  function load() {
    var params = new URLSearchParams({
      min_relevance: els["f-rel"].value,
      local_only: els["f-local"].checked ? "1" : "0",
      limit: 500
    });
    if (els["f-fin"].checked) params.set("with_financials", "1");
    if (els["f-q"].value.trim()) params.set("search", els["f-q"].value.trim());
    if (els["f-pmin"].value) params.set("min_price", els["f-pmin"].value);
    if (els["f-pmax"].value) params.set("max_price", els["f-pmax"].value);
    if (els["f-mach"].value) params.set("min_machines", els["f-mach"].value);
    if (els["f-source"].value) params.set("source", els["f-source"].value);
    if (els["f-days"].value) params.set("since_days", els["f-days"].value);

    ML.api("/api/listings?" + params)
      .then(render)
      .catch(function (e) { ML.toast(e.message, "bad"); });
  }

  var debounced = ML.debounce(load, 250);
  els["f-rel"].oninput = function () { els["f-rel-out"].textContent = els["f-rel"].value; debounced(); };
  els["f-local"].onchange = load;
  els["f-fin"].onchange = load;
  els["f-q"].oninput = debounced;
  ["f-pmin", "f-pmax", "f-mach"].forEach(function (k) { els[k].oninput = debounced; });
  els["f-source"].onchange = load;
  els["f-days"].onchange = load;
  els["btn-clear"].onclick = clearFilters;
  els["btn-more"].onclick = function () {
    els["more-filters"].hidden = !els["more-filters"].hidden;
    els["btn-more"].textContent = els["more-filters"].hidden ? "More filters" : "Fewer filters";
  };

  document.getElementById("btn-find").onclick = function () {
    ML.jobs.start("/api/jobs/find-routes", {}, function (job) {
      load();
      var found = (job.result || {}).found || 0;
      if (!found) {
        ML.toast("No listings came back — see 'Where it looked' below", "info");
      }
    });
  };

  document.getElementById("btn-import").onclick = function () {
    var panel = ML.drawer.open(
      "<div class='card'><div class='card-head'><h2>Import listings</h2>" +
        "<div class='spacer'></div><button class='icon-btn' data-close>&times;</button></div>" +
      "<div class='card-body'>" +
        "<p>Every marketplace offers a free saved search with email alerts. Export " +
          "those results and drop the CSV here — column names are matched loosely, " +
          "so an unedited export usually just works.</p>" +
        "<label class='field'><span class='lbl'>CSV file</span>" +
          "<input type='file' id='imp-file' accept='.csv'></label>" +
        "<label class='field'><span class='lbl'>Label these as</span>" +
          "<input type='text' id='imp-name' value='imported' placeholder='bizbuysell'></label>" +
      "</div>" +
      "<div class='drawer-foot'><button class='btn btn-primary' id='imp-go'>Import</button>" +
        "<button class='btn' data-close>Cancel</button></div></div>", { centered: true });

    panel.querySelector("#imp-go").onclick = function () {
      var file = panel.querySelector("#imp-file").files[0];
      if (!file) { ML.toast("Choose a CSV file first", "bad"); return; }
      var form = new FormData();
      form.append("file", file);
      form.append("source_name", panel.querySelector("#imp-name").value || "imported");
      ML.api("/api/listings/import", { form: form })
        .then(function (res) {
          ML.drawer.close();
          ML.toast("Imported " + res.imported + " listing(s)", "good");
          clearFilters();
        })
        .catch(function (e) { ML.toast(e.message, "bad"); });
    };
  };

  load();
})();
