/* Dashboard: KPI tiles, four charts, activity feed, and the two scan buttons. */
(function () {
  "use strict";

  var GRADE_ORDER = ["A+", "A", "B", "C", "D"];
  // The validated ordinal ramp: one hue, monotone lightness, darker = better.
  var GRADE_VARS = {
    "A+": "var(--grade-ap)", "A": "var(--grade-a)", "B": "var(--grade-b)",
    "C": "var(--grade-c)", "D": "var(--grade-d)"
  };

  var latest = null;

  function kpi(id, value, sub) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
    if (sub != null) {
      var s = document.getElementById(id + "-sub");
      if (s) s.textContent = sub;
    }
  }

  function draw(data) {
    latest = data;
    var stats = data.stats || {};

    kpi("kpi-sites", (stats.sites || 0).toLocaleString(),
      stats.last_site_run ? "last scan " + ML.when(stats.last_site_run) : "not scanned yet");
    kpi("kpi-agrade", (stats.sites_a_grade || 0).toLocaleString());

    var pipeline = data.pipeline || {};
    var working = (pipeline.queued || 0) + (pipeline.contacted || 0) +
      (pipeline.following_up || 0) + (pipeline.interested || 0);
    kpi("kpi-pipeline", working.toLocaleString(),
      (pipeline.won || 0) + " won · " + (pipeline.interested || 0) + " interested");
    kpi("kpi-listings", (stats.listings || 0).toLocaleString(),
      (stats.listings_local || 0) + " in the metro");

    var due = document.getElementById("badge-due");
    if (due) due.textContent = (data.due_today || 0) + " due now";

    // Grades: ordered categories, so the ordinal ramp earns its place here.
    ML.charts.bars("chart-grades", {
      title: "Prospects by grade",
      labelWidth: 46,
      data: GRADE_ORDER.map(function (g) {
        return {
          label: g, value: (data.grades || {})[g] || 0,
          color: GRADE_VARS[g],
          note: g === "A+" ? "your best prospects" : ""
        };
      })
    });

    // Pipeline stages: one series (a count) across ordered stages -> one hue.
    // The server sends an ordered list because these are funnel stages.
    ML.charts.bars("chart-pipeline", {
      title: "Pipeline",
      labelWidth: 96,
      data: (data.stages || []).map(function (s) {
        return { label: s.label, value: s.count, color: "var(--brand)" };
      })
    });

    // A trend needs at least two days. With fewer, the honest form is the
    // number itself, not an empty plot.
    var daily = data.outreach_daily || [];
    var sent = (data.outreach || {}).sent || 0;
    var host = document.getElementById("chart-outreach");
    if (daily.length < 2) {
      host.innerHTML = sent
        ? "<div style='padding:14px 0'><div class='stat-value'>" + sent + "</div>" +
          "<div class='stat-sub'>sent so far — a daily trend appears once you've " +
          "sent on more than one day</div></div>"
        : "<div class='empty' style='padding:26px 0'><h3>No emails sent yet</h3>" +
          "<p class='small'>Pick prospects and start a sequence; sends show up here.</p>" +
          "<a class='btn btn-sm btn-primary' href='/prospects'>Pick prospects</a></div>";
    } else {
      ML.charts.line("chart-outreach", {
        title: "Emails sent per day",
        unit: "sent",
        points: daily.map(function (row) {
          return { label: (row.day || "").slice(5), y: row.n };
        })
      });
    }

    // These averages cluster in a narrow band, so eight bars would all look
    // the same length and say nothing. The ranked numbers are the content.
    var tbody = document.querySelector("#category-table tbody");
    if (tbody) {
      var rows = data.categories || [];
      tbody.innerHTML = rows.length
        ? rows.map(function (row) {
            return "<tr class='clickable' data-category='" + ML.esc(row.category) + "'>" +
              "<td>" + ML.esc(row.category_label) + "</td>" +
              "<td class='num'><strong>" + Number(row.avg_score || 0).toFixed(0) + "</strong></td>" +
              "<td class='num'>" + row.n + "</td></tr>";
          }).join("")
        : "<tr><td colspan='3' class='muted small'>Scan for prospects to fill this in.</td></tr>";
      tbody.querySelectorAll("[data-category]").forEach(function (tr) {
        tr.onclick = function () {
          location.href = "/prospects?category=" + encodeURIComponent(tr.dataset.category);
        };
      });
    }

    renderActivity(data.activities || []);
  }

  function renderActivity(items) {
    var host = document.getElementById("activity-feed");
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<p class="muted small" style="margin:0">' +
        "Nothing yet. Once you start working prospects, every call, note and email lands here.</p>";
      return;
    }
    host.innerHTML = '<ul class="timeline">' + items.map(function (a) {
      return "<li><span class='dot-mark'></span><div style='min-width:0;flex:1'>" +
        "<div class='what'>" + ML.esc(a.title || a.kind) +
        (a.site_name ? " <span class='muted'>— " + ML.esc(a.site_name) + "</span>" : "") + "</div>" +
        (a.body ? "<div class='detail'>" + ML.esc(String(a.body).slice(0, 220)) + "</div>" : "") +
        "<div class='when'>" + ML.when(a.created_at) + "</div>" +
        "</div></li>";
    }).join("") + "</ul>";
  }

  function load() {
    ML.api("/api/stats").then(draw).catch(function (err) {
      ML.toast(err.message, "bad");
    });
  }

  function startScan() {
    ML.jobs.start("/api/jobs/scan", { territories: 4 }, load);
  }

  ["btn-scan", "btn-scan-empty"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.onclick = startScan;
  });
  var routes = document.getElementById("btn-routes");
  if (routes) routes.onclick = function () {
    ML.jobs.start("/api/jobs/find-routes", {}, function (job) {
      load();
      var blocked = (job.result || {}).blocked || [];
      if (blocked.length) {
        ML.toast(blocked.length + " source(s) unavailable — see Routes for sale", "info");
      }
    });
  };

  document.addEventListener("ml:resize", function () { if (latest) draw(latest); });
  document.addEventListener("ml:theme", function () { if (latest) draw(latest); });
  load();
})();
