// Saley dashboard, the controls that change something (R2). Plain JS, no build step.
// Every call here is a POST with the header X-Saley-Act. Only six paths are allowed for a
// control; the page refuses any other path before it sends anything (gate step 9 checks the same
// list). One more call is not a control: after a list is drawn, `markSeen` tells Saley which
// items this person was shown (D75). It has one fixed path and no button.
// A server error never reaches the page as raw text: the status picks a calm line in words.js.
(function () {
  "use strict";

  var UI = window.SaleyUI;
  var W = window.WORDS;
  var fmt = window.fmt;
  var el = UI.el;
  var $ = UI.$;

  var ALLOWED = /^\/api\/(act|undo\/\d+|decision\/\d+\/confirm|proposal\/\d+\/(dismiss|edit)|commitment\/\d+\/done)$/;
  var CALM_MS = 6000;
  var TICK_MS = 1000;
  var SECOND = 1000;
  var MINUTE = 60;
  var TEN = 10;
  var ERROR_LINE = { 403: "errForbidden", 404: "errGone", 409: "errConflict", 422: "errInvalid" };
  var SERVER_LINE = { 403: true, 404: true, 409: true, 422: true };
  var MAX_LINE = 120;
  var UNDO_PATH = /^\/api\/undo\//;
  var toastTimer = null;
  var calmTimer = null;

  // ---------- one POST ----------
  function post(path, body) {
    if (!ALLOWED.test(path)) return Promise.reject({ line: W.errInvalid });
    var headers = { Accept: "application/json", "X-Saley-Act": "1" };
    if (body) headers["Content-Type"] = "application/json";
    return fetch(path, { method: "POST", headers: headers, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" }).then(
      function (r) {
        if (r.ok) return r.json();
        // The server's own line for a refusal (403, 404, 409, 422). It is fixed text from
        // words.py, so it is safe and exact; any other answer gets the calm generic line.
        return r.json().catch(function () { return {}; }).then(function (answer) {
          var fixed = SERVER_LINE[r.status] && typeof answer.detail === "string" && answer.detail.length <= MAX_LINE ? answer.detail : null;
          throw { line: fixed || W[ERROR_LINE[r.status] || "errGeneric"] };
        });
      },
      function () { throw { line: W.errNetwork }; }
    );
  }

  // ---------- seen (D75): not a control, sent after a list is drawn ----------
  var SEEN_PATH = "/api/seen";
  var SEEN_MAX = 200;  // the server takes at most 200 ids in one call
  function markSeen(type, ids) {
    var list = ids.filter(function (i) { return i > 0; }).slice(0, SEEN_MAX);
    if (!list.length) return Promise.resolve(false);
    var headers = { Accept: "application/json", "Content-Type": "application/json", "X-Saley-Act": "1" };
    var body = JSON.stringify({ item_type: type, ids: list });
    return fetch(SEEN_PATH, { method: "POST", headers: headers, body: body, credentials: "same-origin" }).then(
      function (r) { return r.ok; },
      function () { return false; }  // a lost mark only means the item shows again next time
    );
  }

  // ---------- the calm line ----------
  function calm(text, isError) {
    var box = $("calm");
    box.textContent = text;
    box.className = "calm " + (isError ? "bad" : "ok");
    box.hidden = false;
    clearTimeout(calmTimer);
    calmTimer = setTimeout(function () { box.hidden = true; }, CALM_MS);
  }

  // ---------- the toast: "Saved · Undo (9:59)" ----------
  function clock(seconds) {
    var s = Math.max(0, seconds);
    var m = Math.floor(s / MINUTE);
    var r = s % MINUTE;
    return m + ":" + (r < TEN ? "0" : "") + r;
  }
  function hideToast() {
    clearInterval(toastTimer);
    $("toast").hidden = true;
  }
  // `ids` are the write ids to undo, `until` is the server's undo time, `after` reloads the list.
  function showToast(ids, until, after) {
    var box = $("toast"), button = $("toast-undo");
    var end = new Date(until).getTime();
    clearInterval(toastTimer);
    $("toast-text").textContent = W.saved + " ·";
    button.disabled = false;
    button.onclick = function () { undoAll(ids, after); };
    function tick() {
      var left = Math.ceil((end - Date.now()) / SECOND);
      if (left <= 0) { hideToast(); return; }
      button.textContent = fmt(W.undoIn, { t: clock(left) });
    }
    box.hidden = false;
    tick();
    toastTimer = setInterval(tick, TICK_MS);
  }
  function undoAll(ids, after) {
    var button = $("toast-undo");
    button.disabled = true;
    ids.reduce(function (chain, id) {
      return chain.then(function () { return post("/api/undo/" + id); });
    }, Promise.resolve()).then(function () {
      hideToast();
      calm(W.undone, false);
      UI.loadStatus();
      if (after) after();
    }).catch(function (e) {
      button.disabled = false;
      calm(e.line || W.errGeneric, true);
    });
  }

  // ---------- run one control ----------
  // The button is off while the call runs, so one click is one POST. `onDone` gets the answer.
  // `onFail` gets the calm line when the call fails (the editor shows it next to the text).
  function run(button, path, body, onDone, after, onFail) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    return post(path, body).then(function (answer) {
      button.removeAttribute("aria-busy");
      if (UNDO_PATH.test(path)) {
        hideToast();
        calm(W.undone, false);  // an Undo answer says "Undone", never "Saved" (M3)
      } else if (answer.items && answer.items.length) {
        var ids = answer.items.filter(function (i) { return !i.already; }).map(function (i) { return i.write_id; });
        if (ids.length && answer.undo_until) showToast(ids, answer.undo_until, after);
        else calm(W.savedLine, false);
      } else if (answer.write_id && answer.undo_until && !answer.already) {
        showToast([answer.write_id], answer.undo_until, after);  // a signed Done with an undo window
      } else {
        calm(W.savedLine, false);
      }
      UI.loadStatus();
      if (onDone) onDone(answer);
      return answer;
    }).catch(function (e) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      calm(e.line || W.errGeneric, true);
      if (onFail) onFail(e.line || W.errGeneric);
    });
  }

  // A control: type=button always, so a click can never submit a form.
  function control(kind, label, extra) {
    var attrs = { type: "button", cls: "btn act-" + kind, "data-act": kind, text: label };
    Object.keys(extra || {}).forEach(function (k) { attrs[k] = extra[k]; });
    return el("button", attrs);
  }

  // D207: no "View as" select. Every call acts as the signed-in person. Tests that need another
  // person send the X-Saley-As header with the mock provider (D50, D116); the page never does.

  UI.markSeen = markSeen;
  UI.post = post;
  UI.run = run;
  UI.control = control;
  UI.calm = calm;
})();
