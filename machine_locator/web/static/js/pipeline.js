/* Kanban board with drag-and-drop between stages. */
(function () {
  "use strict";

  var board = document.getElementById("board");
  var dragging = null;

  function card(item) {
    var due = item.next_action_at ? ML.dueLabel(item.next_action_at) : "";
    var overdue = due === "overdue" || due === "today";
    return "<article class='kcard' draggable='true' data-id='" + ML.esc(item.id) + "'>" +
      "<div class='top'>" +
        "<span class='grade " + ML.gradeClass(item.grade) + "'>" + Math.round(item.score || 0) + "</span>" +
        "<span class='name' style='min-width:0;flex:1'>" + ML.esc(item.name) + "</span>" +
      "</div>" +
      "<div class='meta'>" + ML.esc(item.category_label || "") + "</div>" +
      (item.contact_email ? "<div class='meta'>" + ML.esc(item.contact_email) + "</div>" : "") +
      (item.next_action
        ? "<div class='next'><span class='status " + (overdue ? "status-warning" : "status-muted") + "'></span>" +
          ML.esc(item.next_action) + (due ? " · " + due : "") + "</div>"
        : "") +
      "</article>";
  }

  function render(data) {
    board.innerHTML = (data.stages || []).map(function (stage) {
      var key = stage.key;
      var items = (data.board || {})[key] || [];
      return "<section class='column' data-stage='" + key + "'>" +
        "<header class='column-head'>" + ML.esc(stage.label) +
          "<span class='count'>" + stage.count + "</span></header>" +
        "<div class='column-body'>" +
          (items.length ? items.map(card).join("")
            : "<p class='muted small' style='padding:6px 4px;margin:0'>Nothing here</p>") +
        "</div></section>";
    }).join("");
    wire();
  }

  function wire() {
    board.querySelectorAll(".kcard").forEach(function (el) {
      el.addEventListener("dragstart", function (e) {
        dragging = el.dataset.id;
        el.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        // Firefox will not start a drag without data set.
        e.dataTransfer.setData("text/plain", el.dataset.id);
      });
      el.addEventListener("dragend", function () {
        el.classList.remove("dragging");
        dragging = null;
      });
      el.addEventListener("click", function () {
        location.href = "/prospects?focus=" + encodeURIComponent(el.dataset.id);
      });
    });

    board.querySelectorAll(".column").forEach(function (col) {
      col.addEventListener("dragover", function (e) {
        e.preventDefault();
        col.classList.add("drag-over");
      });
      col.addEventListener("dragleave", function () { col.classList.remove("drag-over"); });
      col.addEventListener("drop", function (e) {
        e.preventDefault();
        col.classList.remove("drag-over");
        var id = dragging || e.dataTransfer.getData("text/plain");
        if (!id) return;
        var stage = col.dataset.stage;
        ML.api("/api/sites/" + encodeURIComponent(id) + "/pipeline", { body: { stage: stage } })
          .then(function () {
            ML.toast("Moved to " + col.querySelector(".column-head").textContent.trim(), "good");
            load();
          })
          .catch(function (err) { ML.toast(err.message, "bad"); });
      });
    });
  }

  function load() {
    ML.api("/api/pipeline").then(render).catch(function (e) {
      board.innerHTML = "<p class='muted'>" + ML.esc(e.message) + "</p>";
    });
  }

  load();
})();
