/* Settings: save, SMTP test, do-not-contact list. */
(function () {
  "use strict";

  var form = document.getElementById("settings-form");
  var hint = document.getElementById("save-hint");

  form.onsubmit = function (e) {
    e.preventDefault();
    var body = {};
    new FormData(form).forEach(function (value, key) { body[key] = value; });
    hint.textContent = "Saving…";
    ML.api("/api/settings", { body: body }).then(function (res) {
      hint.textContent = "";
      if (res.missing && res.missing.length) {
        ML.toast("Saved. Still needed: " + res.missing.join(", "), "info");
      } else {
        ML.toast("Settings saved", "good");
      }
      setTimeout(function () { location.reload(); }, 700);
    }).catch(function (err) {
      hint.textContent = "";
      ML.toast(err.message, "bad");
    });
  };

  /* Guess the mail server from the email domain, so most people never have to
     look one up. Only fills a blank field -- it never overwrites a choice. */
  var senderEmail = document.getElementById("sender-email");
  var smtpHost = document.getElementById("smtp-host");
  var smtpPort = document.getElementById("smtp-port");
  var GUESSES = {
    "gmail.com": ["smtp.gmail.com", 587], "googlemail.com": ["smtp.gmail.com", 587],
    "outlook.com": ["smtp-mail.outlook.com", 587], "hotmail.com": ["smtp-mail.outlook.com", 587],
    "yahoo.com": ["smtp.mail.yahoo.com", 587], "icloud.com": ["smtp.mail.me.com", 587]
  };
  if (senderEmail) {
    senderEmail.addEventListener("blur", function () {
      var at = senderEmail.value.indexOf("@");
      if (at < 0 || smtpHost.value.trim()) return;
      var guess = GUESSES[senderEmail.value.slice(at + 1).toLowerCase()];
      if (guess) {
        smtpHost.value = guess[0];
        smtpPort.value = guess[1];
        ML.toast("Filled in the mail server for you — check it looks right", "info");
      }
    });
  }

  // Same courtesy for the incoming server: guess it from the email domain.
  var imapHost = document.getElementById("imap-host");
  var imapPort = document.getElementById("imap-port");
  var IMAP_GUESSES = {
    "gmail.com": ["imap.gmail.com", 993], "googlemail.com": ["imap.gmail.com", 993],
    "outlook.com": ["outlook.office365.com", 993], "hotmail.com": ["outlook.office365.com", 993],
    "yahoo.com": ["imap.mail.yahoo.com", 993], "icloud.com": ["imap.mail.me.com", 993]
  };
  if (senderEmail && imapHost) {
    senderEmail.addEventListener("blur", function () {
      var at = senderEmail.value.indexOf("@");
      if (at < 0 || imapHost.value.trim()) return;
      var guess = IMAP_GUESSES[senderEmail.value.slice(at + 1).toLowerCase()];
      if (guess) { imapHost.value = guess[0]; imapPort.value = guess[1]; }
    });
  }

  function testEndpoint(button, outputId, url) {
    var out = document.getElementById(outputId);
    out.textContent = "Testing…";
    out.className = "small muted";
    var body = {};
    new FormData(form).forEach(function (v, k) { body[k] = v; });
    // Save first, so the test uses what's on screen rather than what's stored.
    ML.api("/api/settings", { body: body })
      .then(function () { return ML.api(url, { body: {} }); })
      .then(function (res) {
        out.textContent = res.message;
        out.className = "small " + (res.ok ? "status status-good" : "status status-critical");
      })
      .catch(function (err) {
        out.textContent = err.message;
        out.className = "small status status-critical";
      });
  }

  var imapBtn = document.getElementById("btn-test-imap");
  if (imapBtn) {
    imapBtn.onclick = function () {
      testEndpoint(imapBtn, "imap-result", "/api/settings/test-imap");
    };
  }

  var smtpBtn = document.getElementById("btn-test");
  smtpBtn.onclick = function () {
    testEndpoint(smtpBtn, "test-result", "/api/settings/test-smtp");
  };

  document.getElementById("btn-suppress").onclick = function () {
    var input = document.getElementById("sup-value");
    var value = input.value.trim();
    if (!value) return;
    ML.api("/api/suppression", { body: { value: value, reason: "Added manually" } })
      .then(function () { ML.toast("Added to do-not-contact", "good"); location.reload(); })
      .catch(function (e) { ML.toast(e.message, "bad"); });
  };

  document.querySelectorAll("[data-unsuppress]").forEach(function (btn) {
    btn.onclick = function () {
      ML.api("/api/suppression/" + encodeURIComponent(btn.dataset.unsuppress), { method: "DELETE" })
        .then(function () { ML.toast("Removed", "good"); location.reload(); })
        .catch(function (e) { ML.toast(e.message, "bad"); });
    };
  });
})();
