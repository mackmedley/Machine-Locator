/* Shared front-end helpers: fetch wrapper, toasts, theme, drawer, job polling,
   and the small SVG chart kit the dashboard uses. */
(function () {
  "use strict";

  var ML = window.ML = window.ML || {};

  /* ------------------------------------------------------------- utility */

  ML.esc = function (value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  ML.safeUrl = function (url) {
    return /^https?:\/\//i.test(url || "") ? url : "";
  };

  // Matches the CLI: abbreviate big figures but keep exact dollars below $10K,
  // because those are the numbers people negotiate on.
  ML.money = function (n) {
    if (n == null || n === "") return "—";
    n = Number(n);
    if (!isFinite(n)) return "—";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
    if (n >= 1e4) return "$" + Math.round(n / 1e3) + "K";
    return "$" + Math.round(n).toLocaleString();
  };

  ML.gradeClass = function (grade) {
    return { "A+": "grade-ap", "A": "grade-a", "B": "grade-b", "C": "grade-c", "D": "grade-d" }[grade] || "grade-d";
  };

  ML.gradeBadge = function (site) {
    return '<span class="grade ' + ML.gradeClass(site.grade) + '" title="Score ' +
      Number(site.score).toFixed(1) + ' of 100">' + Math.round(site.score) + "</span>";
  };

  ML.when = function (iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return String(iso).slice(0, 16).replace("T", " ");
    var diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.round(diff / 60) + "m ago";
    if (diff < 86400) return Math.round(diff / 3600) + "h ago";
    if (diff < 604800) return Math.round(diff / 86400) + "d ago";
    return d.toLocaleDateString();
  };

  ML.dueLabel = function (iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var days = Math.round((d.getTime() - Date.now()) / 86400000);
    if (days < 0) return "overdue";
    if (days === 0) return "today";
    if (days === 1) return "tomorrow";
    return "in " + days + " days";
  };

  ML.debounce = function (fn, ms) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms || 220);
    };
  };

  /* ---------------------------------------------------------------- api */

  ML.api = function (url, options) {
    options = options || {};
    var init = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
      init.method = options.method || "POST";
    }
    if (options.form) { init.body = options.form; init.method = "POST"; }
    return fetch(url, init).then(function (response) {
      var type = response.headers.get("content-type") || "";
      if (!type.includes("application/json")) {
        if (!response.ok) throw new Error("Server error (" + response.status + ")");
        return response;
      }
      return response.json().then(function (data) {
        if (!response.ok || data.error) {
          throw new Error(data.error || "Request failed (" + response.status + ")");
        }
        return data;
      });
    });
  };

  /* -------------------------------------------------------------- toasts */

  ML.toast = function (message, kind) {
    var host = document.querySelector(".toasts");
    if (!host) {
      host = document.createElement("div");
      host.className = "toasts";
      document.body.appendChild(host);
    }
    var el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    el.innerHTML = '<div style="flex:1">' + ML.esc(message) + "</div>" +
      '<button aria-label="Dismiss">&times;</button>';
    el.querySelector("button").onclick = function () { el.remove(); };
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === "bad" ? 9000 : 5000);
  };

  /* --------------------------------------------------------------- theme */

  ML.theme = {
    get: function () {
      try { return localStorage.getItem("ml-theme") || "system"; }
      catch (e) { return "system"; }
    },
    set: function (value) {
      try { localStorage.setItem("ml-theme", value); } catch (e) { /* private mode */ }
      ML.theme.apply(value);
    },
    apply: function (value) {
      if (value === "system") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", value);
      var btn = document.getElementById("theme-toggle");
      if (btn) btn.title = "Theme: " + value;
      document.dispatchEvent(new CustomEvent("ml:theme"));
    },
    cycle: function () {
      var order = ["system", "light", "dark"];
      var next = order[(order.indexOf(ML.theme.get()) + 1) % order.length];
      ML.theme.set(next);
      ML.toast("Theme: " + next, "info");
    }
  };
  ML.theme.apply(ML.theme.get());

  /* -------------------------------------------------------------- drawer */

  ML.drawer = {
    open: function (html, options) {
      ML.drawer.close();
      options = options || {};
      var backdrop = document.createElement("div");
      backdrop.className = "drawer-backdrop";
      backdrop.onclick = ML.drawer.close;

      var panel = document.createElement("aside");
      panel.className = options.centered ? "modal" : "drawer";
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
      panel.innerHTML = html;

      document.body.appendChild(backdrop);
      document.body.appendChild(panel);
      document.body.style.overflow = "hidden";
      ML.drawer._nodes = [backdrop, panel];

      panel.querySelectorAll("[data-close]").forEach(function (el) {
        el.onclick = ML.drawer.close;
      });
      var focusable = panel.querySelector("input, textarea, button, select");
      if (focusable && !options.noFocus) focusable.focus();
      return panel;
    },
    close: function () {
      (ML.drawer._nodes || []).forEach(function (n) { n.remove(); });
      ML.drawer._nodes = null;
      document.body.style.overflow = "";
    },
    isOpen: function () { return !!ML.drawer._nodes; }
  };
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && ML.drawer.isOpen()) ML.drawer.close();
  });

  /* ---------------------------------------------------------------- jobs */

  ML.jobs = {
    poll: null,
    /* Starts a background job and shows a progress strip until it finishes. */
    start: function (url, body, onDone) {
      return ML.api(url, { body: body || {} })
        .then(function (data) {
          ML.jobs.watch(data.job_id, onDone);
          return data;
        })
        .catch(function (err) { ML.toast(err.message, "bad"); throw err; });
    },
    watch: function (jobId, onDone) {
      var strip = ML.jobs._strip();
      clearInterval(ML.jobs.poll);
      var tick = function () {
        ML.api("/api/jobs/" + jobId).then(function (job) {
          ML.jobs._render(strip, job);
          if (job.status === "done" || job.status === "failed") {
            clearInterval(ML.jobs.poll);
            setTimeout(function () { strip.remove(); }, 2500);
            if (job.status === "done") {
              ML.toast(job.message || "Finished", "good");
              if (onDone) onDone(job);
            } else {
              ML.toast(job.message + ": " + (job.error || "").split("\n")[0], "bad");
            }
          }
        }).catch(function () { clearInterval(ML.jobs.poll); });
      };
      tick();
      ML.jobs.poll = setInterval(tick, 1200);
    },
    _strip: function () {
      var existing = document.getElementById("job-strip");
      if (existing) return existing;
      var strip = document.createElement("div");
      strip.className = "job-strip";
      strip.id = "job-strip";
      var page = document.querySelector(".page");
      if (page) page.insertBefore(strip, page.firstChild.nextSibling || page.firstChild);
      return strip;
    },
    _render: function (strip, job) {
      var pct = job.total > 0 ? Math.round((job.progress / job.total) * 100) : 0;
      strip.innerHTML =
        '<div class="spinner"></div>' +
        '<div style="min-width:0;flex:1">' +
          '<div style="font-weight:560;margin-bottom:5px">' + ML.esc(job.message || "Working...") + "</div>" +
          '<div class="progress' + (pct ? "" : " indeterminate") + '"><span style="width:' + (pct || 35) + '%"></span></div>' +
        "</div>";
    },
    resume: function () {
      /* Reattach to a job still running from a previous page view. */
      ML.api("/api/jobs/active").then(function (data) {
        if (data.job) ML.jobs.watch(data.job.id, function () { location.reload(); });
      }).catch(function () {});
    }
  };

  /* -------------------------------------------------------------- charts */

  var chartTip = null;
  function showTip(html, event) {
    if (!chartTip) {
      chartTip = document.createElement("div");
      chartTip.className = "chart-tip";
      document.body.appendChild(chartTip);
    }
    chartTip.innerHTML = html;
    chartTip.style.display = "block";
    var rect = chartTip.getBoundingClientRect();
    var x = Math.min(event.clientX + 14, window.innerWidth - rect.width - 8);
    var y = Math.max(8, event.clientY - rect.height - 12);
    chartTip.style.left = x + "px";
    chartTip.style.top = y + "px";
  }
  function hideTip() { if (chartTip) chartTip.style.display = "none"; }
  ML.hideTip = hideTip;

  function svgEl(name, attrs) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  /* A rounded-end horizontal bar: square where it meets the baseline, 4px
     rounded at the data end, per the mark spec. */
  function barPath(x, y, w, h, r) {
    r = Math.min(r, w, h / 2);
    if (w <= 0.5) return "M" + x + "," + y + "h0";
    return "M" + x + "," + y +
      "h" + (w - r) +
      "a" + r + "," + r + " 0 0 1 " + r + "," + r +
      "v" + (h - 2 * r) +
      "a" + r + "," + r + " 0 0 1 " + (-r) + "," + r +
      "h" + (-(w - r)) + "z";
  }

  ML.charts = {
    /* Horizontal bars. One colour per bar only when the categories are an
       ordered scale (grades); otherwise every bar is the same hue, because a
       value-ramp on nominal categories double-encodes the bar length. */
    bars: function (host, options) {
      host = typeof host === "string" ? document.getElementById(host) : host;
      if (!host) return;
      var data = options.data || [];
      host.innerHTML = "";
      if (!data.length) {
        host.innerHTML = '<p class="muted small" style="padding:8px 0">No data yet.</p>';
        return;
      }
      var rowH = options.rowHeight || 30;
      var gap = 2;                      // 2px surface gap between adjacent bars
      var labelW = options.labelWidth || 116;
      var valueW = 46;
      var width = host.clientWidth || 520;
      var height = data.length * rowH;
      var plotW = Math.max(40, width - labelW - valueW);
      var max = Math.max.apply(null, data.map(function (d) { return d.value; })) || 1;

      var svg = svgEl("svg", {
        viewBox: "0 0 " + width + " " + height, height: height,
        role: "img", "aria-label": options.title || "Bar chart"
      });

      data.forEach(function (d, i) {
        var y = i * rowH;
        var barH = rowH - gap * 2 - 8;
        var w = Math.max(2, (d.value / max) * plotW);

        var label = svgEl("text", {
          x: labelW - 10, y: y + rowH / 2 + 4, "text-anchor": "end", class: "axis-text"
        });
        label.textContent = d.label;
        svg.appendChild(label);

        var track = svgEl("rect", {
          x: labelW, y: y + (rowH - barH) / 2, width: plotW, height: barH,
          rx: 4, fill: "var(--surface-2)"
        });
        svg.appendChild(track);

        var bar = svgEl("path", {
          d: barPath(labelW, y + (rowH - barH) / 2, w, barH, 4),
          fill: d.color || "var(--brand)"
        });
        bar.style.cursor = "default";
        svg.appendChild(bar);

        var value = svgEl("text", {
          x: labelW + plotW + 8, y: y + rowH / 2 + 4, class: "value-text"
        });
        value.textContent = d.display != null ? d.display : d.value.toLocaleString();
        svg.appendChild(value);

        var hit = svgEl("rect", {
          x: 0, y: y, width: width, height: rowH, fill: "transparent"
        });
        hit.addEventListener("mousemove", function (e) {
          showTip("<div class='t'>" + ML.esc(d.label) + "</div><strong>" +
            ML.esc(d.display != null ? d.display : d.value.toLocaleString()) +
            "</strong>" + (d.note ? "<div class='t'>" + ML.esc(d.note) + "</div>" : ""), e);
        });
        hit.addEventListener("mouseleave", hideTip);
        svg.appendChild(hit);
      });

      host.appendChild(svg);
    },

    /* Single-series trend line with a crosshair and tooltip. */
    line: function (host, options) {
      host = typeof host === "string" ? document.getElementById(host) : host;
      if (!host) return;
      var points = options.points || [];
      host.innerHTML = "";
      if (points.length < 2) {
        host.innerHTML = '<p class="muted small" style="padding:18px 0;text-align:center">' +
          (options.emptyText || "Not enough history yet.") + "</p>";
        return;
      }
      var width = host.clientWidth || 520;
      var height = options.height || 170;
      var pad = { top: 12, right: 12, bottom: 26, left: 34 };
      var plotW = width - pad.left - pad.right;
      var plotH = height - pad.top - pad.bottom;
      var max = Math.max.apply(null, points.map(function (p) { return p.y; })) || 1;
      max = Math.max(max, 1);

      var svg = svgEl("svg", {
        viewBox: "0 0 " + width + " " + height, height: height,
        role: "img", "aria-label": options.title || "Trend"
      });

      var ticks = 3;
      for (var t = 0; t <= ticks; t++) {
        var value = Math.round((max / ticks) * t);
        var y = pad.top + plotH - (value / max) * plotH;
        svg.appendChild(svgEl("line", {
          x1: pad.left, y1: y, x2: width - pad.right, y2: y, class: "grid-line"
        }));
        var lbl = svgEl("text", { x: pad.left - 7, y: y + 4, "text-anchor": "end", class: "axis-text" });
        lbl.textContent = value;
        svg.appendChild(lbl);
      }

      var xAt = function (i) { return pad.left + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW); };
      var yAt = function (v) { return pad.top + plotH - (v / max) * plotH; };

      var d = points.map(function (p, i) {
        return (i ? "L" : "M") + xAt(i).toFixed(1) + "," + yAt(p.y).toFixed(1);
      }).join("");
      svg.appendChild(svgEl("path", { d: d, class: "series-line", stroke: "var(--brand)" }));

      [0, points.length - 1].forEach(function (i) {
        var lbl = svgEl("text", {
          x: xAt(i), y: height - 8, class: "axis-text",
          "text-anchor": i === 0 ? "start" : "end"
        });
        lbl.textContent = points[i].label;
        svg.appendChild(lbl);
      });

      var crosshair = svgEl("line", {
        x1: 0, y1: pad.top, x2: 0, y2: pad.top + plotH,
        stroke: "var(--border-strong)", "stroke-width": 1, opacity: 0
      });
      svg.appendChild(crosshair);
      var marker = svgEl("circle", {
        r: 4.5, class: "dot", fill: "var(--brand)", opacity: 0
      });
      svg.appendChild(marker);

      var hit = svgEl("rect", {
        x: pad.left, y: pad.top, width: plotW, height: plotH, fill: "transparent"
      });
      hit.addEventListener("mousemove", function (e) {
        var box = svg.getBoundingClientRect();
        var rel = ((e.clientX - box.left) / box.width) * width;
        var i = Math.round(((rel - pad.left) / plotW) * (points.length - 1));
        i = Math.max(0, Math.min(points.length - 1, i));
        var p = points[i];
        crosshair.setAttribute("x1", xAt(i)); crosshair.setAttribute("x2", xAt(i));
        crosshair.setAttribute("opacity", 1);
        marker.setAttribute("cx", xAt(i)); marker.setAttribute("cy", yAt(p.y));
        marker.setAttribute("opacity", 1);
        showTip("<div class='t'>" + ML.esc(p.label) + "</div><strong>" +
          p.y + " " + ML.esc(options.unit || "") + "</strong>", e);
      });
      hit.addEventListener("mouseleave", function () {
        crosshair.setAttribute("opacity", 0);
        marker.setAttribute("opacity", 0);
        hideTip();
      });
      svg.appendChild(hit);
      host.appendChild(svg);
    }
  };

  /* ------------------------------------------------------------- startup */

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.onclick = ML.theme.cycle;
    ML.jobs.resume();

    // "/" focuses the page search box, wherever there is one.
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
        var search = document.querySelector('input[type="search"]');
        if (search) { e.preventDefault(); search.focus(); }
      }
    });
  });

  window.addEventListener("resize", ML.debounce(function () {
    document.dispatchEvent(new CustomEvent("ml:resize"));
  }, 200));
})();
