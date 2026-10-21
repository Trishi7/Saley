// Saley dashboard, the team views of R2: my tasks (Today), Inbox, Commitments, Decisions.
// Every value from the API goes into the page with textContent (el() does that), never as HTML.
// Every visible word comes from words.js or from the API, which gets it from words.py.
(function () {
  "use strict";

  var UI = window.SaleyUI;
  var W = window.WORDS;
  var fmt = window.fmt;
  var el = UI.el;
  var $ = UI.$;
  var clear = UI.clear;
  var LIMIT = 200;
  var commitState = "open";
  var UNDO_FILTERS = [["", W.fAny], ["open", W.undoOpen], ["undone", W.undoUndone], ["undo_failed", W.undoFailed], ["closed", W.undoClosed]];
  var SURFACE_FILTERS = [["", W.fAny], ["dashboard", W.surfDashboard], ["chat", W.surfChat], ["card", W.surfCard]];
  var STATE_WORDS = { proposed: W.stProposed, confirmed: W.stConfirmed, active: W.stActive };
  var COMMIT_WORDS = { open: W.cTabOpen, done: W.cTabDone, dropped: W.cDropped };
  var UNDO_WORDS = { open: W.undoOpen, undone: W.undoUndone, undo_failed: W.undoFailed, closed: W.undoClosed };
  var SYNC_WORDS = { pending: W.syncPending, held: W.syncHeld, done: W.syncDone, failed: W.syncFailed, cancelled: W.syncCancelled };
  var SYNC_PILL = { pending: "warn", held: "warn", done: "ok", failed: "bad", cancelled: "gray" };

  function done(button) {
    button.textContent = W.doneState;
    button.disabled = true;
  }

  // ---------- Today: my tasks ----------
  function taskButton(t, row) {
    if (t.undo_id) {
      var undo = UI.control("undo", W.undoBtn, { "data-endpoint": "/api/undo/" + t.undo_id });
      undo.addEventListener("click", function () {
        UI.run(undo, "/api/undo/" + t.undo_id, null, function () { row.classList.add("is-done"); done(undo); }, null);
      });
      return undo;
    }
    if (t.action) {
      var tick = UI.control("done", W.doneBtn, {
        "data-endpoint": t.action.endpoint,
        "aria-label": fmt(W.doneAria, { t: t.title })
      });
      tick.addEventListener("click", function () {
        UI.run(tick, t.action.endpoint, t.action.body, function () { row.classList.add("is-done"); done(tick); }, UI.loadToday);
      });
      return tick;
    }
    // Add email has no Done (D154). The new app gets the field; here a plain line says why.
    if (t.input_role && t.soon) return el("span", { cls: "pill gray", "data-soon": t.soon, text: W.comingSoon });
    return t.link ? el("a", { href: "#" + t.link.view, text: W.openInbox }) : null;
  }
  function taskRow(t) {
    var row = el("li", { cls: "task", "data-key": t.key, "data-kind": t.kind });
    var main = el("div", { cls: "t-main" }, el("span", { cls: "t-title", text: t.title }));
    if (t.why) main.appendChild(el("span", { cls: "t-why muted", text: t.why }));
    row.appendChild(main);
    if (t.due_label) row.appendChild(el("span", { cls: "t-due stamp", text: t.due_label }));
    row.appendChild(el("span", { cls: "t-act" }, taskButton(t, row)));
    return row;
  }
  UI.renderTasks = function (block) {
    var list = clear($("tasks"));
    if (!block.items.length) list.appendChild(el("li", { cls: "empty", text: block.empty }));
    block.items.forEach(function (t) { list.appendChild(taskRow(t)); });
    $("tasks-more").textContent = block.total > block.items.length ? fmt(W.tasksShown, { n: block.items.length, m: block.total }) : "";
  };

  // ---------- Inbox ----------
  // ---------- Inbox: the inline card editor (D129, D137) ----------
  // Only an action card with one draft cell can be edited. The server locks the card, so the
  // first saver wins; the other one gets 409 with "Already done by X at HH:MM", shown as is.
  var CARD_ID = /^\/api\/proposal\/(\d+)\/dismiss$/;
  function draftOf(item) {
    var match = item.kind === "card" && item.dismiss && CARD_ID.exec(item.dismiss);
    var payload = item.action && item.action.body && item.action.body.payload;
    var cells = payload && payload.cells;
    if (!match || !cells || cells.length !== 1) return null;
    return { id: match[1], text: cells[0].value };
  }
  function editor(draft, card, reload) {
    var path = "/api/proposal/" + draft.id + "/edit";
    var area = el("textarea", { maxlength: "200", "aria-label": W.editLabel });
    area.value = draft.text;
    var line = el("div", { cls: "edit-line", role: "status" });
    var save = UI.control("edit-save", W.editSave, { "data-endpoint": path });
    var cancel = el("button", { type: "button", cls: "btn", text: W.editCancel });
    var box = el("div", { cls: "edit-row" }, area, line, el("div", { cls: "edit-btns" }, save, cancel));
    save.addEventListener("click", function () {
      var text = area.value.trim();
      if (!text) { line.textContent = W.editFailed; return; }
      line.textContent = "";
      UI.run(save, path, { text: text }, function () {
        card.classList.add("is-done");
        box.remove();
      }, reload, function (said) { line.textContent = said; });
    });
    cancel.addEventListener("click", function () { box.remove(); });
    return box;
  }

  function inboxCard(item, reload) {
    var card = el("div", { cls: "card inbox-card", "data-kind": item.kind, "data-id": item.id });
    var main = el("div", { cls: "ic-main" }, el("b", { text: item.title }));
    if (item.why) main.appendChild(el("div", { cls: "muted", text: item.why }));
    if (item.for_user) main.appendChild(el("div", { cls: "stamp", text: fmt(W.forUser, { u: item.for_user }) }));
    if (item.evidence && item.evidence.row) main.appendChild(el("div", { cls: "stamp", text: fmt(W.dSheetRow, { r: item.evidence.row }) }));
    var acts = el("div", { cls: "ic-act" });
    var dismiss = null;
    if (item.action) {
      var primary = UI.control("primary", item.button || W.confirmBtn, { "data-endpoint": item.action.endpoint });
      primary.addEventListener("click", function () {
        UI.run(primary, item.action.endpoint, item.action.body, function () {
          card.classList.add("is-done");
          done(primary);
          if (dismiss) dismiss.disabled = true;
        }, reload);
      });
      acts.appendChild(primary);
    }
    var draft = draftOf(item);
    if (draft) {
      var edit = el("button", { type: "button", cls: "btn", text: W.editBtn });
      edit.addEventListener("click", function () {
        if (!card.querySelector(".edit-row")) main.appendChild(editor(draft, card, reload));
      });
      acts.appendChild(edit);
    }
    if (item.dismiss) {
      dismiss = UI.control("dismiss", W.dismissBtn, { "data-endpoint": item.dismiss });
      dismiss.addEventListener("click", function () {
        UI.run(dismiss, item.dismiss, null, function () { card.remove(); reload(); }, null);
      });
      acts.appendChild(dismiss);
    }
    card.appendChild(main);
    card.appendChild(acts);
    return card;
  }
  function loadInbox() {
    var box = $("inbox-list");
    UI.api("/api/inbox?limit=" + LIMIT).then(function (d) {
      clear(box);
      $("inbox-count").textContent = fmt(W.inboxCount, { n: d.total });
      $("cnt-inbox").textContent = d.total || "";
      if (!d.items.length) box.appendChild(el("div", { cls: "empty", text: d.empty }));
      d.items.forEach(function (i) { box.appendChild(inboxCard(i, loadInbox)); });
    }).catch(function (e) { UI.fail(box, e); });
  }

  // ---------- Commitments ----------
  function commitRow(c) {
    var state = el("td", null, UI.pill(COMMIT_WORDS[c.state] || c.state, c.state === "open" ? "acc" : "gray"));
    if (c.in_pack) state.appendChild(el("span", { cls: "muted", text: " " + W.cInPack }));
    var act = el("td");
    if (c.state === "open") {
      var mark = UI.control("commitment-done", W.cMarkDone, { "data-endpoint": "/api/commitment/" + c.id + "/done" });
      mark.addEventListener("click", function () {
        UI.run(mark, "/api/commitment/" + c.id + "/done", null, loadCommitments, loadCommitments);
      });
      act.appendChild(mark);
    }
    return el("tr", { "data-id": c.id }, el("td", { text: c.text }), el("td", { text: c.owner_name }),
      el("td", { text: c.due_label || W.cNoDue }), el("td", { text: c.source }), state, act);
  }
  function loadCommitments() {
    var tbody = $("commit-table").querySelector("tbody");
    UI.tabs($("commit-tabs"), [["open", W.cTabOpen], ["done", W.cTabDone], ["all", W.cTabAll]], commitState, function (s) { commitState = s; loadCommitments(); });
    UI.api("/api/commitments?state=" + commitState + "&limit=" + LIMIT).then(function (d) {
      clear(tbody);
      if (!d.items.length) tbody.appendChild(el("tr", null, el("td", { cls: "empty", colspan: "6", text: W.cEmpty })));
      d.items.forEach(function (c) { tbody.appendChild(commitRow(c)); });
    }).catch(function (e) { UI.fail(tbody, e); });
  }

  // ---------- Decisions ----------
  function fillSelect(select, options, keep) {
    var before = keep ? select.value : "";
    clear(select);
    options.forEach(function (o) { select.appendChild(el("option", { value: o[0], text: o[1] })); });
    select.value = before;
  }
  function decisionText(d) {
    var parts = [d.label, d.text, d.what && d.value != null ? d.what + " = " + d.value : d.what, d.person];
    return parts.filter(Boolean).join(" · ");
  }
  function decisionActions(d) {
    var cell = el("td");
    if (d.can_confirm) {
      var ok = UI.control("confirm", W.confirmBtn, { "data-endpoint": "/api/decision/" + d.id + "/confirm" });
      ok.addEventListener("click", function () { UI.run(ok, "/api/decision/" + d.id + "/confirm", null, loadDecisions, null); });
      cell.appendChild(ok);
    }
    if (d.can_undo) {
      var undo = UI.control("undo", W.undoBtn, { "data-endpoint": "/api/undo/" + d.id });
      undo.addEventListener("click", function () { UI.run(undo, "/api/undo/" + d.id, null, loadDecisions, null); });
      cell.appendChild(undo);
    }
    return cell;
  }
  function decisionRow(d) {
    var state = el("td", null, UI.pill(STATE_WORDS[d.state] || d.state, d.state === "proposed" ? "warn" : "gray"));
    if (UNDO_WORDS[d.undo_state] && d.kind !== "strategy_decision" && d.kind !== "dismiss") {
      state.appendChild(document.createTextNode(" "));
      state.appendChild(UI.pill(UNDO_WORDS[d.undo_state], d.undo_state === "open" ? "acc" : d.undo_state === "undo_failed" ? "bad" : "gray"));
    }
    if (d.sync_state && SYNC_WORDS[d.sync_state]) {
      state.appendChild(document.createTextNode(" "));
      var sync = UI.pill(SYNC_WORDS[d.sync_state], SYNC_PILL[d.sync_state]);
      sync.setAttribute("data-sync", d.sync_state);
      state.appendChild(sync);
    }
    if (d.sync_reason) state.appendChild(el("div", { cls: "muted", text: d.sync_reason }));
    return el("tr", { "data-id": d.id, "data-kind": d.kind }, el("td", { text: UI.timeLabel(d.at) }), el("td", { text: d.user_name || "—" }),
      el("td", { text: decisionText(d) }), state, decisionActions(d));
  }
  function decisionQuery() {
    var q = ["limit=" + LIMIT];
    [["person", "dec-person"], ["surface", "dec-surface"], ["undo", "dec-undo"]].forEach(function (f) {
      var v = $(f[1]).value;
      if (v) q.push(f[0] + "=" + encodeURIComponent(v));
    });
    return q.join("&");
  }
  function loadDecisions() {
    var tbody = $("dec-table").querySelector("tbody");
    var people = [["", W.fAny]].concat(UI.state.people.map(function (p) { return [p.short, p.name]; }));
    if ($("dec-person").options.length !== people.length) fillSelect($("dec-person"), people, true);
    if (!$("dec-surface").options.length) fillSelect($("dec-surface"), SURFACE_FILTERS, true);
    if (!$("dec-undo").options.length) fillSelect($("dec-undo"), UNDO_FILTERS, true);
    UI.api("/api/decisions?" + decisionQuery()).then(function (d) {
      clear(tbody);
      if (!d.items.length) tbody.appendChild(el("tr", null, el("td", { cls: "empty", colspan: "5", text: W.dEmpty })));
      d.items.forEach(function (row) { tbody.appendChild(decisionRow(row)); });
    }).catch(function (e) { UI.fail(tbody, e); });
  }

  UI.loaders.asks = loadInbox;
  UI.loaders.commit = loadCommitments;
  UI.loaders.decisions = loadDecisions;
  // The Inbox number in the left rail follows the status poll.
  UI.onStatus = function () {
    UI.api("/api/inbox?limit=1").then(function (d) { $("cnt-inbox").textContent = d.total || ""; }).catch(function () { $("cnt-inbox").textContent = ""; });
  };
  document.addEventListener("DOMContentLoaded", function () {
    ["dec-person", "dec-surface", "dec-undo"].forEach(function (id) { $(id).addEventListener("change", loadDecisions); });
  });
})();
