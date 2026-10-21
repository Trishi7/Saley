// Saley dashboard, the Ask card on Today (R4, D131). Plain JS, no build step.
// One typed question goes to POST /api/ask. It is not a [data-act] control: it has one fixed
// path and sends a question only. The answer is server text, so it goes in with textContent and
// the CSS rule white-space: pre-line keeps its line breaks. It is never HTML.
(function () {
  "use strict";

  var UI = window.SaleyUI;
  var W = window.WORDS;
  var fmt = window.fmt;
  var $ = UI.$;
  var ASK_PATH = "/api/ask";
  var MAX_QUESTION = 300;
  var LINE_BY_STATUS = { 401: "askSignedOut", 422: "askBad", 429: "askBusy" };
  var busy = false;

  function setState(text) { $("ask-state").textContent = text; }

  function fail(status) {
    $("ask-answer").hidden = true;
    setState(W[LINE_BY_STATUS[status] || "askDown"]);
  }

  function show(answer) {
    var box = $("ask-answer");
    box.textContent = answer.text;  // textContent only: the answer is data
    box.hidden = false;
    setState(answer.left_out > 0 ? fmt(W.askLeftOut, { n: answer.left_out }) : "");
  }

  function send() {
    var input = $("ask-input"), button = $("ask-send");
    var text = input.value.trim();
    if (busy) return;
    if (!text) { setState(W.askEmpty); return; }
    busy = true;
    button.disabled = true;
    setState(W.askWorking);
    var headers = { Accept: "application/json", "Content-Type": "application/json", "X-Saley-Act": "1" };
    fetch(ASK_PATH, { method: "POST", headers: headers, body: JSON.stringify({ text: text.slice(0, MAX_QUESTION) }), credentials: "same-origin" }).then(
      function (r) {
        if (!r.ok) { fail(r.status); return null; }
        return r.json().then(show);
      },
      function () { setState(W.errNetwork); }
    ).catch(function () { setState(W.askDown); }).then(function () {
      busy = false;
      button.disabled = false;
    });
  }

  function init() {
    var input = $("ask-input"), button = $("ask-send");
    input.placeholder = W.askPlaceholder;
    input.setAttribute("aria-label", W.askLabel);
    button.textContent = W.askSend;
    button.addEventListener("click", send);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); send(); } });
  }
  init();
})();
