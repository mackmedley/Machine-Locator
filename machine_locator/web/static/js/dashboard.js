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

  /* "Today" is deliberately separate from the KPI tiles: those are a status
     report, this is a to-do list, and mixing them buries the second one. */
  function renderToday(data) {
    var card = document.getElementById("today-card");
    var body = document.getElementById("today-body");
    if (!card || !body) return;

    var blocks = [];

    if (data.due_emails) {
      blocks.push(
        "<div class='today-item'>" +
          "<span class='today-n'>" + data.due_emails + "</span>" +
          "<div><strong>email" + (data.due_emails === 1 ? "" : "s") + " ready to go out</strong>" +
            "<div class='muted small'>" +
              (data.can_send ? "Review and send whenever you're ready."
                             : ML.esc((data.send_blocked_because || [])[0] || "Sending is paused.")) +
            "</div></div>" +
          "<a class='btn btn-sm " + (data.can_send ? "btn-primary" : "") +
            "' href='/outreach'>" + (data.can_send ? "Send them" : "Fix it") + "</a>" +
        "</div>");
    }

    if (data.missing_contacts) {
      blocks.push(
        "<div class='today-item'>" +
          "<span class='today-n'>" + data.missing_contacts + "</span>" +
          "<div><strong>good prospects with no email yet</strong>" +
            "<div class='muted small'>Machine Locator can read their websites and " +
            "fill these in for you.</div></div>" +
          "<a class='btn btn-sm' href='/prospects'>Find them</a>" +
        "</div>");
    }

    (data.replies || []).forEach(function (r) {
      blocks.push(
        "<div class='today-item'>" +
          "<span class='grade " + ML.gradeClass(r.grade) + "'>" + Math.round(r.score) + "</span>" +
          "<div><strong>" + ML.esc(r.name) + " replied</strong>" +
            "<div class='muted small'>Waiting since " + ML.when(r.updated_at) +
              " — a warm lead going cold is the expensive one.</div></div>" +
          (r.phone ? "<a class='btn btn-sm' href='tel:" + ML.esc(r.phone) + "'>Call</a>"
                   : "<a class='btn btn-sm' href='/pipeline'>Open</a>") +
        "</div>");
    });

    (data.actions || []).forEach(function (a) {
      blocks.push(
        "<div class='today-item'>" +
          "<span class='grade " + ML.gradeClass(a.grade) + "'>" + Math.round(a.score) + "</span>" +
          "<div><strong>" + ML.esc(a.name) + "</strong>" +
            "<div class='muted small'>" + ML.esc(a.next_action || "Follow up") +
              " · " + ML.esc(ML.dueLabel(a.next_action_at)) + "</div></div>" +
          ((a.contact_phone || a.phone)
            ? "<a class='btn btn-sm' href='tel:" + ML.esc(a.contact_phone || a.phone) + "'>Call</a>"
            : "<a class='btn btn-sm' href='/pipeline'>Open</a>") +
        "</div>");
    });

    if (data.failed) {
      blocks.push(
        "<div class='today-item'>" +
          "<span class='today-n' style='color:var(--critical)'>" + data.failed + "</span>" +
          "<div><strong>email" + (data.failed === 1 ? "" : "s") + " failed to send</strong>" +
            "<div class='muted small'>Usually a bad address or a mail-server rejection.</div></div>" +
          "<a class='btn btn-sm' href='/outreach'>Look</a>" +
        "</div>");
    }

    if (!blocks.length) { card.hidden = true; return; }
    card.hidden = false;
    // Six is about as much as reads as a to-do list rather than a backlog.
    var SHOWN = 6;
    body.innerHTML = blocks.slice(0, SHOWN).join("") +
      (blocks.length > SHOWN
        ? "<p class='muted small' style='margin:11px 0 0'>and " +
          (blocks.length - SHOWN) + " more — <a href='/pipeline'>see the pipeline</a>.</p>"
        : "");
  }

  function load() {
    ML.api("/api/stats").then(draw).catch(function (err) {
      ML.toast(err.message, "bad");
    });
    ML.api("/api/today").then(renderToday).catch(function () { /* non-critical */ });
  }

  function startScan() {
    ML.jobs.start("/api/jobs/scan", { territories: 4 }, load);
  }

  /* One button for the whole run. It still shows exactly what will go out
     before anything sends -- the point is removing the picking, not removing
     the chance to look. */
  function autopilot() {
    ML.api("/api/autopilot/plan?count=20").then(function (p) {
      var blocked = (p.blocked_reasons || []).length;
      var willSend = Math.min(p.ready.length + p.need_lookup.length, p.remaining);

      var warn = "";
      if (blocked) {
        warn = "<div class='banner banner-warn'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9'><path d='M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z'/></svg>" +
          "<div class='spacer'>" + p.blocked_reasons.map(ML.esc).join("<br>") +
          "</div><a class='btn btn-sm' href='/settings'>Settings</a></div>";
      } else if (!p.has_ever_sent) {
        warn = "<div class='banner banner-warn'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9'><path d='M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z'/></svg>" +
          "<div class='spacer'><strong>You haven't sent anything yet.</strong> " +
          "Read the email below — that is exactly what goes out, with your details " +
          "already in it. Once you're happy, this runs on its own.</div></div>";
      }

      var body =
        warn +
        "<div class='grid grid-3' style='margin-bottom:14px'>" +
          "<div class='stat'><div class='stat-label'>Will write to</div>" +
            "<div class='stat-value'>" + willSend + "</div>" +
            "<div class='stat-sub'>best prospects not yet approached</div></div>" +
          "<div class='stat'><div class='stat-label'>Already have an email</div>" +
            "<div class='stat-value'>" + p.ready.length + "</div>" +
            "<div class='stat-sub'>" + p.need_lookup.length + " need looking up first</div></div>" +
          "<div class='stat'><div class='stat-label'>Today's cap</div>" +
            "<div class='stat-value'>" + p.remaining + "</div>" +
            "<div class='stat-sub'>left of " + p.daily_cap + "</div></div>" +
        "</div>" +
        (p.sample
          ? "<h3 style='margin:0 0 8px'>What " + ML.esc(p.sample.name) + " receives</h3>" +
            "<div class='preview-mail'><div class='subject'>" + ML.esc(p.sample.subject) +
            "</div><div class='body'>" + ML.esc(p.sample.body) + "</div></div>" +
            "<p class='muted small'>Each one is written for that business by name. " +
            "Your postal address and an opt-out are added when it sends.</p>"
          : "<p class='muted'>No prospects left to approach. Run a scan, or lower the " +
            "score cut-off.</p>") +
        "<p class='muted small'>Anyone who replies is taken out of the sequence " +
        "automatically. Anyone who asks to stop is never contacted again.</p>";

      var panel = ML.drawer.open(
        "<div class='card'><div class='card-head'><h2>Find and email prospects</h2>" +
          "<div class='spacer'></div><button class='icon-btn' data-close>&times;</button></div>" +
        "<div class='card-body'>" + body + "</div>" +
        "<div class='drawer-foot'>" +
          "<button class='btn btn-primary' id='ap-go'" +
            (blocked || !willSend ? " disabled" : "") + ">Send to " + willSend + "</button>" +
          "<button class='btn' id='ap-dry'" + (!willSend ? " disabled" : "") +
            ">Practice run, send nothing</button>" +
          "<div style='flex:1'></div><button class='btn btn-ghost' data-close>Cancel</button>" +
        "</div></div>", { centered: true });

      function go(dry) {
        ML.drawer.close();
        ML.jobs.start("/api/jobs/autopilot", { count: 20, dry_run: dry }, function (job) {
          load();
          var r = job.result || {};
          if ((r.skipped || []).length) {
            ML.drawer.open(
              "<div class='card'><div class='card-head'><h2>" + ML.esc(r.summary) + "</h2>" +
                "<div class='spacer'></div><button class='icon-btn' data-close>&times;</button></div>" +
              "<div class='card-body'><p class='muted small'>" + r.skipped.length +
                " were left out — mostly no contact details published anywhere. " +
                "Those need a phone call; the script is on the Outreach page.</p>" +
                "<ul class='small muted' style='padding-left:18px'>" +
                r.skipped.map(function (s) {
                  return "<li>" + ML.esc(s.name) + " — " + ML.esc(s.reason) + "</li>";
                }).join("") + "</ul></div></div>", { centered: true });
          }
        });
      }

      var goBtn = panel.querySelector("#ap-go");
      var dryBtn = panel.querySelector("#ap-dry");
      if (goBtn) goBtn.onclick = function () { go(false); };
      if (dryBtn) dryBtn.onclick = function () { go(true); };
    }).catch(function (e) { ML.toast(e.message, "bad"); });
  }

  var autoBtn = document.getElementById("btn-auto");
  if (autoBtn) autoBtn.onclick = autopilot;

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
