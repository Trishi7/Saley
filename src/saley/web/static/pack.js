// Saley dashboard, the Weekly pack page (R4, D117). Plain JS, no build step.
// It reads GET /api/pack and draws the stored body. Every value goes in with textContent (el()).
// A number with no value shows a dash, never 0. Every visible word comes from words.js.
(function () {
  "use strict";

  var UI = window.SaleyUI;
  var W = window.WORDS;
  var fmt = window.fmt;
  var el = UI.el;
  var $ = UI.$;
  var clear = UI.clear;
  var NO_PACK = /^No pack/;  // the server's 404 line (words.py PACK_NOT_FOUND)

  function lastLine(n) {
    if (n.last_week != null) return fmt(W.packLastWeek, { v: n.last_week });
    return n.last_week_note || "—";
  }
  function tile(n) {
    return el("div", { cls: "tile", "data-key": n.key },
      el("div", { cls: "l", text: n.label }),
      el("div", { cls: "v", text: UI.num(n.value) }),
      el("div", { cls: "d", text: lastLine(n) }));
  }
  function section(title, hint) {
    var h = el("h2", null, el("span", { text: title }));
    if (hint) h.appendChild(el("span", { cls: "hint", text: " " + hint }));
    return h;
  }
  function table(heads, rows) {
    var head = el("tr");
    heads.forEach(function (h) { head.appendChild(el("th", { cls: h[1] || "", text: h[0] })); });
    var body = el("tbody");
    rows.forEach(function (r) { body.appendChild(r); });
    return el("div", { cls: "tw" }, el("table", null, el("thead", null, head), body));
  }
  function owner(o) {
    if (!o) return W.packNoOwner;
    var found = UI.state.people.filter(function (p) { return p.short === o; })[0];
    return found ? found.name : o;
  }

  function lateBlock(body) {
    var box = el("div", { cls: "card mt", id: "pack-late" }, section(W.packLateTitle));
    if (!body.late.length) { box.appendChild(el("p", { cls: "empty", text: W.packLateNone })); return box; }
    var rows = body.late.map(function (l) {
      var by = l.late_by_days === 1 ? W.packLateByOne : fmt(W.packLateBy, { n: l.late_by_days });
      return el("tr", null, el("td", { text: l.item }), el("td", { text: owner(l.owner) }), el("td", { text: UI.dayLabel(l.due) }),
        el("td", { cls: "num", text: by }), el("td", { text: l.blocks.join(", ") }));
    });
    box.appendChild(table([["Item"], ["Owner"], ["Due"], ["Late by", "num"], ["Blocks"]], rows));
    if (body.late_total > body.late.length) box.appendChild(el("p", { cls: "stamp", text: fmt(W.packLateShown, { n: body.late.length, m: body.late_total }) }));
    return box;
  }
  function remindBlock(body) {
    var box = el("div", { cls: "card mt", id: "pack-reminders" }, section(W.packRemindTitle));
    if (!body.packed_reminders.length) { box.appendChild(el("p", { cls: "empty", text: W.packRemindNone })); return box; }
    var rows = body.packed_reminders.map(function (r) {
      return el("tr", null, el("td", { text: r.text }), el("td", { text: owner(r.owner) }), el("td", { text: UI.dayLabel(r.due) }));
    });
    box.appendChild(table([["Promise"], ["Owner"], ["Due"]], rows));
    box.appendChild(el("p", { cls: "stamp", text: fmt(W.packRemindShown, { n: body.packed_reminders.length, m: body.packed_total }) }));
    return box;
  }
  function tasksBlock(body) {
    var box = el("div", { cls: "card mt", id: "pack-tasks" }, section(W.packTasksTitle));
    var grid = el("div", { cls: "grid2" });
    body.tasks.forEach(function (p) {
      var list = el("ul", { cls: "feed" });
      if (!p.items.length) list.appendChild(el("li", { cls: "empty", text: W.packTasksNone }));
      p.items.forEach(function (t) {
        var li = el("li", null, el("span", { text: t.title }));
        if (t.due_label) li.appendChild(el("span", { cls: "stamp", text: " " + t.due_label }));
        list.appendChild(li);
      });
      grid.appendChild(el("div", null, el("b", { text: owner(p.person) + " · " }),
        el("span", { cls: "stamp", text: window.fmtCount(W.packTasksOne, W.packTasksCount, p.total) }), list));
    });
    box.appendChild(grid);
    return box;
  }
  function scoreBlock(body) {
    var box = el("div", { cls: "card mt", id: "pack-score" }, section(W.packScoreTitle));
    if (!body.scorecard.length) { box.appendChild(el("p", { cls: "empty", text: W.packScoreNone })); return box; }
    box.appendChild(table([["Industry"], ["People", "num"], ["Contacted", "num"], ["Connect %", "num"], ["Reply %", "num"], ["Meetings", "num"], ["Deals", "num"], [W.scLastWeek]],
      body.scorecard.map(UI.scoreRow)));
    return box;
  }

  function render(d) {
    var b = d.body;
    UI.setStamp($("pack-stamp"), d);
    $("pack-week").textContent = fmt(W.packWeekOf, { day: UI.dayLabel(b.week_start), last: UI.dayLabel(b.last_week_day) });
    var box = clear($("pack-box"));
    box.appendChild(section(W.packTeamTitle, W.packTeamHint));
    var tiles = el("div", { cls: "tiles", id: "pack-tiles" });
    b.team.forEach(function (n) { tiles.appendChild(tile(n)); });
    box.appendChild(tiles);
    box.appendChild(lateBlock(b));
    box.appendChild(remindBlock(b));
    box.appendChild(tasksBlock(b));
    box.appendChild(scoreBlock(b));
  }

  function loadPack() {
    var box = $("pack-box");
    UI.api("/api/pack").then(render).catch(function (e) {
      if (NO_PACK.test(e.message)) {
        $("pack-week").textContent = "";
        clear(box).appendChild(el("div", { cls: "empty", text: W.packNone }));
      } else UI.fail(box, e);
    });
  }

  UI.loaders.pack = loadPack;
})();
