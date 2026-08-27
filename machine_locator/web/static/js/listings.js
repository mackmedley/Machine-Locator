/* Routes-for-sale browser with deal maths and CSV import. */
(function () {
  "use strict";

  var els = {
    rows: document.getElementById("rows"),
    empty: document.getElementById("empty-state"),
    count: document.getElementById("count"),
    local: document.getElementById("f-local"),
    rel: document.getElementById("f-rel"),
    relOut: document.getElementById("f-rel-out"),
    price: document.getElementById("f-price")
  };

  function render(listings) {
    els.count.textContent = listings.length + " listing(s)";
    els.empty.innerHTML = "";
    if (!listings.length) {
      els.rows.innerHTML = "";
      els.empty.innerHTML =
        "<div class='empty'><h3>No routes match</h3>" +
        "<p>Loosen the filters, hit <strong>Check marketplaces</strong>, or import a " +
        "saved-search export from a broker with <strong>Import CSV</strong>.</p></div>";
      return;
    }

    els.rows.innerHTML = listings.map(function (l) {
      var perMachine = (l.price && l.machine_count) ? ML.money(l.price / l.machine_count) : "—";
      var multiple = (l.price && l.cash_flow) ? (l.price / l.cash_flow).toFixed(1) + "×" : "—";
      var href = ML.safeUrl(l.url);
      var title = href
        ? "<a href='" + ML.esc(href) + "' target='_blank' rel='noopener'>" + ML.esc(l.title) + "</a>"
        : ML.esc(l.title);
      return "<tr>" +
        "<td class='num'><strong>" + Math.round(l.relevance) + "</strong></td>" +
        "<td style='max-width:420px'>" + title +
          (l.is_local ? " <span class='badge' style='background:var(--good-soft);color:var(--good);border-color:transparent'>metro</span>" : "") +
          "<div class='sub'>" + ML.esc((l.relevance_reasons || []).join(" · ")) + "</div></td>" +
        "<td class='num'>" + ML.money(l.price) + "</td>" +
        "<td class='num'>" + ML.money(l.cash_flow) + "</td>" +
        "<td class='num'>" + (l.machine_count || "—") + "</td>" +
        "<td class='num'>" + perMachine + "</td>" +
        "<td class='num'>" + multiple + "</td>" +
        "<td class='small'>" + ML.esc(l.location_text || "—") + "</td>" +
        "<td class='small'>" + ML.esc(l.source) + "</td>" +
        "<td class='small nowrap'>" + ML.esc((l.first_seen || "").slice(0, 10)) + "</td>" +
      "</tr>";
    }).join("");
  }

  function load() {
    var params = new URLSearchParams({
      min_relevance: els.rel.value,
      local_only: els.local.checked ? "1" : "0",
      limit: 500
    });
    if (els.price.value) params.set("max_price", els.price.value);
    ML.api("/api/listings?" + params)
      .then(function (data) { render(data.listings || []); })
      .catch(function (e) { ML.toast(e.message, "bad"); });
  }

  els.rel.oninput = function () { els.relOut.textContent = els.rel.value; debounced(); };
  els.local.onchange = load;
  els.price.oninput = ML.debounce(load, 300);
  var debounced = ML.debounce(load, 200);

  document.getElementById("btn-find").onclick = function () {
    ML.jobs.start("/api/jobs/find-routes", {}, function (job) {
      load();
      var blocked = (job.result || {}).blocked || [];
      if (blocked.length) {
        ML.drawer.open(
          "<div class='card'><div class='card-head'><h2>Some sources didn't respond</h2>" +
          "<div class='spacer'></div><button class='icon-btn' data-close>&times;</button></div>" +
          "<div class='card-body'><p class='muted small'>This is normal — several marketplaces " +
          "block automated access. Set up their free email alerts and use <strong>Import CSV</strong> instead.</p>" +
          "<ul class='small' style='padding-left:18px;color:var(--text-2)'>" +
          blocked.map(function (b) { return "<li>" + ML.esc(b) + "</li>"; }).join("") +
          "</ul></div></div>", { centered: true });
      }
    });
  };

  document.getElementById("btn-import").onclick = function () {
    var panel = ML.drawer.open(
      "<div class='card'><div class='card-head'><h2>Import listings</h2>" +
        "<div class='spacer'></div><button class='icon-btn' data-close>&times;</button></div>" +
      "<div class='card-body'>" +
        "<p>Every marketplace offers a free saved search with email alerts. Export those " +
          "results and drop the CSV here — column names are matched loosely, so an unedited " +
          "export usually just works.</p>" +
        "<label class='field'><span class='lbl'>CSV file</span><input type='file' id='imp-file' accept='.csv'></label>" +
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
          load();
        })
        .catch(function (e) { ML.toast(e.message, "bad"); });
    };
  };

  load();
})();
