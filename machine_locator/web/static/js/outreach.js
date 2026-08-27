/* Outreach queue, template editor, and the send buttons. */
(function () {
  "use strict";

  var els = {
    rows: document.getElementById("queue-rows"),
    empty: document.getElementById("queue-empty"),
    count: document.getElementById("queue-count"),
    status: document.getElementById("f-status"),
    due: document.getElementById("f-due"),
    gate: document.getElementById("gate-banner")
  };

  var STATUS_CLASS = {
    sent: "status-good", queued: "status-muted",
    failed: "status-critical", cancelled: "status-muted", draft: "status-warning"
  };

  function renderGate(gate) {
    if (!gate) return;
    if (gate.allowed) {
      els.gate.innerHTML =
        "<div class='banner banner-info'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9'><path d='M20 6L9 17l-5-5'/></svg>" +
        "<div class='spacer'><strong>Ready to send</strong>" +
        gate.remaining + " of " + gate.daily_cap + " left in today's cap.</div></div>";
    } else {
      els.gate.innerHTML =
        "<div class='banner banner-warn'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9'><path d='M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z'/></svg>" +
        "<div class='spacer'><strong>Sending is paused</strong>" +
        gate.reasons.map(ML.esc).join("<br>") + "</div>" +
        "<a class='btn btn-sm' href='/settings'>Fix in settings</a></div>";
    }
  }

  function render(data) {
    var messages = data.messages || [];
    var stats = data.stats || {};
    document.getElementById("s-queued").textContent = stats.queued || 0;
    document.getElementById("s-sent").textContent = stats.sent || 0;
    document.getElementById("s-failed").textContent = stats.failed || 0;
    // What is actually due right now, not everything ever queued.
    document.getElementById("s-due").textContent = stats.due != null ? stats.due : "—";
    document.getElementById("s-today").textContent =
      (data.gate ? data.gate.sent_today + " today" : "");
    renderGate(data.gate);

    els.count.textContent = messages.length + " message(s)";
    els.empty.innerHTML = "";
    if (!messages.length) {
      els.rows.innerHTML = "";
      els.empty.innerHTML =
        "<div class='empty'><h3>Nothing queued</h3>" +
        "<p>Go to <strong>Prospects</strong>, tick the businesses you want to approach, " +
        "and hit <em>Start outreach</em>. Each one gets a three-email sequence you can read first.</p>" +
        "<a class='btn btn-primary' href='/prospects'>Pick prospects</a></div>";
      return;
    }

    els.rows.innerHTML = messages.map(function (m) {
      var when = m.status === "sent" ? ML.when(m.sent_at)
        : (m.scheduled_at ? ML.dueLabel(m.scheduled_at) : "now");
      return "<tr data-id='" + m.id + "'>" +
        "<td><strong>" + ML.esc(m.site_name || "—") + "</strong>" +
          (m.site_address ? "<div class='sub'>" + ML.esc(m.site_address) + "</div>" : "") + "</td>" +
        "<td class='small'>" + ML.esc(m.to_address || "—") + "</td>" +
        "<td>" + ML.esc(m.subject || "") +
          (m.error ? "<div class='sub' style='color:var(--critical)'>" + ML.esc(m.error) + "</div>" : "") + "</td>" +
        "<td class='num'>" + ((m.step || 0) + 1) + "</td>" +
        "<td class='small nowrap'>" + ML.esc(when) + "</td>" +
        "<td><span class='status " + (STATUS_CLASS[m.status] || "status-muted") + "'>" + ML.esc(m.status) + "</span></td>" +
        "<td class='nowrap'><button class='btn btn-sm btn-ghost' data-view='" + m.id + "'>View</button>" +
          (m.status === "queued" ? "<button class='btn btn-sm btn-ghost' data-send='" + m.id + "'>Send now</button>" : "") +
          (m.status === "failed" ? "<button class='btn btn-sm btn-ghost' data-requeue='" + m.id + "'>Retry</button>" : "") +
        "</td></tr>";
    }).join("");

    els.rows.querySelectorAll("[data-view]").forEach(function (b) {
      b.onclick = function () { view(messages.find(function (m) { return m.id == b.dataset.view; })); };
    });
    els.rows.querySelectorAll("[data-send]").forEach(function (b) {
      b.onclick = function () {
        b.disabled = true;
        ML.api("/api/outreach/send/" + b.dataset.send, { body: {} })
          .then(function () { ML.toast("Sent", "good"); load(); })
          .catch(function (e) { ML.toast(e.message, "bad"); b.disabled = false; });
      };
    });
    els.rows.querySelectorAll("[data-requeue]").forEach(function (b) {
      b.onclick = function () {
        ML.api("/api/outreach/messages/" + b.dataset.requeue, { body: { action: "requeue" } })
          .then(load).catch(function (e) { ML.toast(e.message, "bad"); });
      };
    });
  }

  function view(m) {
    if (!m) return;
    var editable = m.status === "queued" || m.status === "draft";
    var panel = ML.drawer.open(
      "<div class='drawer-head'><div style='flex:1'><h2>" + ML.esc(m.site_name || "Message") + "</h2>" +
        "<div class='muted small'>to " + ML.esc(m.to_address || "—") + "</div></div>" +
        "<button class='icon-btn' data-close>&times;</button></div>" +
      "<div class='drawer-body'>" +
        "<label class='field'><span class='lbl'>Subject</span>" +
          "<input id='m-subject' type='text' value='" + ML.esc(m.subject || "") + "'" +
          (editable ? "" : " disabled") + "></label>" +
        "<label class='field'><span class='lbl'>Message</span>" +
          "<textarea id='m-body' rows='16'" + (editable ? "" : " disabled") + ">" +
          ML.esc(m.body || "") + "</textarea>" +
          "<span class='help'>Your postal address and an opt-out line are appended automatically when this sends.</span></label>" +
      "</div>" +
      "<div class='drawer-foot'>" +
        (editable ? "<button class='btn btn-primary' id='m-save'>Save changes</button>" +
                    "<button class='btn' id='m-send'>Send now</button>" +
                    "<div style='flex:1'></div>" +
                    "<button class='btn btn-danger' id='m-cancel'>Cancel this email</button>" : "") +
      "</div>");

    if (!editable) return;
    panel.querySelector("#m-save").onclick = function () {
      ML.api("/api/outreach/messages/" + m.id, {
        body: { subject: panel.querySelector("#m-subject").value, body: panel.querySelector("#m-body").value }
      }).then(function () { ML.toast("Saved", "good"); ML.drawer.close(); load(); })
        .catch(function (e) { ML.toast(e.message, "bad"); });
    };
    panel.querySelector("#m-send").onclick = function () {
      ML.api("/api/outreach/send/" + m.id, { body: {} })
        .then(function () { ML.toast("Sent", "good"); ML.drawer.close(); load(); })
        .catch(function (e) { ML.toast(e.message, "bad"); });
    };
    panel.querySelector("#m-cancel").onclick = function () {
      ML.api("/api/outreach/messages/" + m.id, { body: { action: "cancel" } })
        .then(function () { ML.toast("Cancelled", "good"); ML.drawer.close(); load(); })
        .catch(function (e) { ML.toast(e.message, "bad"); });
    };
  }

  /* ------------------------------------------------------------ templates */

  function loadTemplates() {
    ML.api("/api/templates").then(function (data) {
      var host = document.getElementById("template-list");
      host.innerHTML = (data.templates || []).map(function (t) {
        return "<div class='card'><div class='card-head'>" +
          "<h2>" + ML.esc(t.name) + "</h2>" +
          "<span class='badge'>" + ML.esc(t.channel === "script" ? "script" : "email") + "</span>" +
          (t.delay_days ? "<span class='badge'>day " + t.delay_days + "</span>" : "") +
          "<div class='spacer'></div>" +
          "<button class='btn btn-sm' data-edit='" + ML.esc(t.key) + "'>Edit</button></div>" +
          "<div class='card-body'>" +
            (t.channel === "email" ? "<div class='small' style='font-weight:580;margin-bottom:6px'>" + ML.esc(t.subject) + "</div>" : "") +
            "<div class='small muted' style='white-space:pre-wrap;max-height:190px;overflow:hidden'>" +
              ML.esc((t.body || "").slice(0, 600)) + "</div>" +
          "</div></div>";
      }).join("");

      host.querySelectorAll("[data-edit]").forEach(function (b) {
        b.onclick = function () {
          var t = data.templates.find(function (x) { return x.key === b.dataset.edit; });
          editTemplate(t);
        };
      });
    }).catch(function (e) { ML.toast(e.message, "bad"); });
  }

  function editTemplate(t) {
    var panel = ML.drawer.open(
      "<div class='drawer-head'><div style='flex:1'><h2>" + ML.esc(t.name) + "</h2>" +
        "<div class='muted small'>Merge fields like <code>{business_name}</code> fill in per prospect</div></div>" +
        "<button class='icon-btn' data-close>&times;</button></div>" +
      "<div class='drawer-body'>" +
        (t.channel === "email"
          ? "<label class='field'><span class='lbl'>Subject</span><input id='t-subject' type='text' value='" + ML.esc(t.subject || "") + "'></label>"
          : "") +
        "<label class='field'><span class='lbl'>Body</span>" +
          "<textarea id='t-body' rows='20' class='mono'>" + ML.esc(t.body || "") + "</textarea></label>" +
        (t.delay_days !== undefined && t.channel === "email"
          ? "<label class='field'><span class='lbl'>Send this many days after the first email</span>" +
            "<input id='t-delay' type='number' min='0' max='90' value='" + (t.delay_days || 0) + "'></label>"
          : "") +
      "</div>" +
      "<div class='drawer-foot'><button class='btn btn-primary' id='t-save'>Save template</button>" +
        "<button class='btn' data-close>Cancel</button></div>");

    panel.querySelector("#t-save").onclick = function () {
      var body = { key: t.key, name: t.name, channel: t.channel,
                   sequence_key: t.sequence_key, step: t.step,
                   body: panel.querySelector("#t-body").value };
      var subject = panel.querySelector("#t-subject");
      if (subject) body.subject = subject.value;
      var delay = panel.querySelector("#t-delay");
      if (delay) body.delay_days = Number(delay.value);
      ML.api("/api/templates", { body: body })
        .then(function () { ML.toast("Template saved", "good"); ML.drawer.close(); loadTemplates(); })
        .catch(function (e) { ML.toast(e.message, "bad"); });
    };
  }

  /* --------------------------------------------------------------- wiring */

  function load() {
    var params = new URLSearchParams();
    if (els.status.value) params.set("status", els.status.value);
    if (els.due.checked) params.set("due", "1");
    ML.api("/api/outreach/messages?" + params).then(render)
      .catch(function (e) { ML.toast(e.message, "bad"); });
  }

  els.status.onchange = load;
  els.due.onchange = load;

  document.getElementById("btn-send").onclick = function () {
    ML.jobs.start("/api/jobs/send-queue", { dry_run: false }, load);
  };
  document.getElementById("btn-dry-run").onclick = function () {
    ML.jobs.start("/api/jobs/send-queue", { dry_run: true }, function (job) {
      ML.toast("Dry run: " + (job.result || {}).summary, "info");
      load();
    });
  };

  document.querySelectorAll(".tabs button").forEach(function (btn) {
    btn.onclick = function () {
      document.querySelectorAll(".tabs button").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      document.querySelectorAll("[data-panel]").forEach(function (p) {
        p.hidden = p.dataset.panel !== btn.dataset.tab;
      });
      if (btn.dataset.tab === "templates") loadTemplates();
    };
  });

  load();
})();
