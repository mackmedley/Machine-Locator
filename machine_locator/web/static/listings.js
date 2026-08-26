/* Routes-for-sale browser. */
(function () {
  var els = {
    localOnly: document.getElementById("local-only"),
    minRelevance: document.getElementById("min-relevance"),
    minRelevanceOut: document.getElementById("min-relevance-out"),
    maxPrice: document.getElementById("max-price"),
    rows: document.getElementById("listing-rows"),
    count: document.getElementById("listing-count"),
    empty: document.getElementById("listing-empty")
  };

  function money(n) {
    // Mirrors the CLI: abbreviate big figures, but keep exact dollars below
    // $10K so a per-machine price is never rounded into uselessness.
    if (n == null) return "-";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
    if (n >= 1e4) return "$" + Math.round(n / 1e3) + "K";
    return "$" + Math.round(Number(n)).toLocaleString();
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function safeUrl(u) {
    // Only http(s) links get rendered as anchors.
    return /^https?:\/\//i.test(u || "") ? u : "";
  }

  function render(listings) {
    els.rows.innerHTML = "";
    els.empty.hidden = listings.length > 0;
    listings.forEach(function (l) {
      var perMachine = l.price && l.machine_count ? money(l.price / l.machine_count) : "-";
      var href = safeUrl(l.url);
      var title = href
        ? '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener">' + escapeHtml(l.title) + "</a>"
        : escapeHtml(l.title);
      var tr = document.createElement("tr");
      if (l.is_local) tr.className = "local";
      tr.innerHTML =
        '<td class="num">' + Math.round(l.relevance) + "</td>" +
        "<td>" + title +
        '<div class="why">' + escapeHtml((l.relevance_reasons || []).join(" · ")) + "</div></td>" +
        '<td class="num">' + money(l.price) + "</td>" +
        '<td class="num">' + money(l.cash_flow) + "</td>" +
        '<td class="num">' + (l.machine_count || "-") + "</td>" +
        '<td class="num">' + perMachine + "</td>" +
        "<td>" + escapeHtml(l.location_text || "-") + "</td>" +
        "<td>" + escapeHtml(l.source) + "</td>" +
        "<td>" + escapeHtml((l.first_seen || "").slice(0, 10)) + "</td>";
      els.rows.appendChild(tr);
    });
    els.count.textContent = listings.length + " listing(s)";
  }

  function load() {
    var params = new URLSearchParams({
      min_relevance: els.minRelevance.value,
      local_only: els.localOnly.checked ? "1" : "0",
      limit: 500
    });
    if (els.maxPrice.value) params.set("max_price", els.maxPrice.value);
    fetch("/api/listings?" + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) { render(data.listings || []); })
      .catch(function () { els.count.textContent = "Could not load listings."; });
  }

  els.minRelevance.addEventListener("input", function () {
    els.minRelevanceOut.textContent = els.minRelevance.value;
    load();
  });
  els.localOnly.addEventListener("change", load);
  els.maxPrice.addEventListener("input", load);

  load();
})();
