// Saley dashboard, release R2. Plain JS, no build step (docs/08 "Stack").
// This file reads. act.js holds the POST calls (Done, Undo, Confirm, Dismiss, Mark done) and
// team.js draws the task list and the Inbox, Commitments and Decisions views.
// Every value from the API goes into the page with textContent, never as HTML: sheet
// cells are data typed by people, not markup. Every visible word comes from words.js.
(function () {
  "use strict";

  var W = window.WORDS;
  var fmt = window.fmt;
  var UI = window.SaleyUI = {};
  var PAGE = 200;
  var LONG = 160;          // text longer than this shows as bullets (R1.1 A.5)
  var BULLETS = 6;         // bullets shown before "Show all"
  var BLOCKERS_SHOWN = 4;  // goal card blockers before "+N more" (R1.1 A.5a)
  var STATUS_POLL_MS = 60000;
  var STATUS_RETRY_MS = 30000;  // after a network error: a server restart heals the bar by itself
  var PENDING_POLL_MS = 3000;  // while a write waits for the worker, look again soon
  var state = { syncId: null, people: [], peopleFilter: "backlog", accFilter: "all", pplOffset: 0, accOffset: 0, role: "viewer" };
  var STAGE_PILL = { Won: "ok", Qualified: "ok", Meeting: "ok", Engaged: "acc", Connected: "acc", Contacted: "gray", Listed: "gray", Parked: "warn", Lost: "bad" };
  var RAG_PILL = { "on track": "ok", "at risk": "warn", "blocked": "bad" };
  var TOUCH_WORDS = { first_contact: "First contact", li_req: "LinkedIn request", li_accept: "LinkedIn accepted", email1: "Email 1", email2: "Email 2", email3: "Email 3", li_dm: "LinkedIn DM", meeting: "Meeting", call: "Call", reply: "Reply", bounce: "Bounce" };
  var OWNER_SOURCE = { first_contact: W.ownerSrcFirst, li_addition: W.ownerSrcLi, first_contact_type: W.ownerSrcType, "default": W.ownerSrcDefault };
  var PEOPLE_TABS = [["backlog", W.fBacklog], ["p1_waiting", W.fP1], ["in_conversation", W.fConversation], ["reconnect_due", W.fReconnect], ["contacted_this_week", W.fWeek], ["all", W.fAll]];
  var GROUPS = [["people", W.gPeople], ["companies", W.gCompanies], ["goals", W.gGoals], ["deliverables", W.gDeliverables], ["decisions", W.gDecisions]];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  // The data each view loads, for "Could not load {what}." (never a bare "Failed to fetch").
  var WHAT = { today: W.whatToday, goals: W.whatGoals, accounts: W.whatAccounts, people: W.whatPeople, suggest: W.whatSuggest, signals: W.whatSignals, blockers: W.whatBlockers, money: W.whatMoney, events: W.whatEvents, asks: W.whatAsks, commit: W.whatCommit, decisions: W.whatDecisions, pack: W.whatPack, scorecard: W.whatScorecard, sources: W.whatSources, search: W.whatSearch };

  // ---------- small helpers ----------
  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k] == null ? "" : String(attrs[k]);
      else if (k === "cls") node.className = attrs[k];
      else if (k === "on") Object.keys(attrs.on).forEach(function (ev) { node.addEventListener(ev, attrs.on[ev]); });
      else node.setAttribute(k, attrs[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null) continue;
      node.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function $(id) { return document.getElementById(id); }
  function pill(text, cls) { return el("span", { cls: "pill " + (cls || "gray"), text: text }); }
  function stagePill(s) { return pill(s || "—", STAGE_PILL[s] || "gray"); }
  function num(v) { return v == null ? "—" : String(v); }
  function dayLabel(iso) {
    if (!iso) return "—";
    var p = String(iso).slice(0, 10).split("-");
    var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
    return DAYS[d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()];
  }
  function timeLabel(iso) { return iso ? dayLabel(iso) + " " + String(iso).slice(11, 16) : "—"; }
  function debounce(fn, ms) { var t; return function () { var a = arguments; clearTimeout(t); t = setTimeout(function () { fn.apply(null, a); }, ms); }; }
  function link(href, text) { return el("a", { href: href, target: "_blank", rel: "noopener noreferrer", text: text }); }
  function noneOnFile() { return el("span", { cls: "muted", text: W.noneOnFile }); }
  // Owners are stored as short codes ("sid"); show the team member's name.
  function ownerName(o) {
    if (!o) return o;
    var m = state.people.filter(function (p) { return p.short.toLowerCase() === String(o).toLowerCase(); })[0];
    return m ? m.name : o;
  }
  function usd(v) { return "USD " + Math.round(v).toLocaleString("en-US"); }

  // Long text (> 160 characters) becomes bullets: split on new lines, ";" and ". ".
  function longText(text) {
    var value = String(text || "");
    if (value.length <= LONG) return document.createTextNode(value);
    var parts = value.split(/\n+|;\s*|\.\s+/).map(function (s) { return s.trim(); }).filter(Boolean);
    var wrap = el("div", { cls: "bullets" });
    var list = el("ul");
    parts.forEach(function (p, i) { list.appendChild(el("li", { cls: i >= BULLETS ? "extra" : "", text: p })); });
    wrap.appendChild(list);
    if (parts.length > BULLETS) {
      var btn = el("button", { cls: "link", type: "button", text: W.showAll });
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var open = wrap.classList.toggle("open");
        btn.textContent = open ? W.showLess : W.showAll;
      });
      wrap.appendChild(btn);
    }
    return wrap;
  }

  // Bold the matched part of a name, with text nodes only (no HTML from data).
  function marked(name, match) {
    var span = el("span");
    if (!match) { span.textContent = name; return span; }
    var a = match[0], n = match[1];
    span.appendChild(document.createTextNode(name.slice(0, a)));
    span.appendChild(el("mark", { text: name.slice(a, a + n) }));
    span.appendChild(document.createTextNode(name.slice(a + n)));
    return span;
  }

  function detailText(d) { if (!d) return ""; if (typeof d === "string") return d; return (d[0] && d[0].msg) || W.badRequest; }
  // D40: every call after the first sends the snapshot the page read first. If the sheet
  // changed since, the API answers 409 and the page asks for a reload instead of mixing data.
  // The status bar always reads the newest snapshot, so it never sends one.
  // D207: no "View as". Every call is the signed-in person's; the page never sends X-Saley-As.
  // A fetch that the server does not answer throws a TypeError ("Failed to fetch"). It becomes
  // W.noServer with `network` set, so the page names the data and never shows the raw text.
  function noServer() { var e = new Error(W.noServer); e.network = true; return e; }
  function api(path) {
    var pinned = state.syncId != null && path.indexOf("/api/status") !== 0;
    var url = pinned ? path + (path.indexOf("?") < 0 ? "?" : "&") + "sync_id=" + state.syncId : path;
    var headers = { Accept: "application/json" };
    return fetch(url, { headers: headers, credentials: "same-origin" }).then(function (r) {
      if (r.status === 409) showChanged();
      if (r.status === 401) location.href = "/signin";  // the session ended (R4, D114)
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (b) { throw new Error(detailText(b.detail) || ("HTTP " + r.status)); });
      return r.json().then(function (body) {
        if (state.syncId == null && body.sync_id != null && path.indexOf("/api/status") !== 0) state.syncId = body.sync_id;
        return body;
      });
    }, function () { throw noServer(); });
  }
  function showChanged() { $("changed").hidden = false; }
  // "Could not load {what}. {reason}" and a Retry button that runs the load again. `what` and
  // `retry` default to the view the node is in and that view's loader (show()).
  function loadError(what, err) { return fmt(W.loadFailed, { what: what }) + " " + (err && err.message ? err.message : W.noServer); }
  function retryButton(onRetry) {
    return el("button", { type: "button", cls: "btn", "data-retry": "", text: W.retryBtn, on: { click: onRetry } });
  }
  function fail(node, err, what, retry) {
    var view = node.closest ? node.closest(".view") : null;
    var name = view ? view.id.replace(/^v-/, "") : "";
    var line = loadError(what || WHAT[name] || W.whatPage, err);
    clear(node).appendChild(el("div", { cls: "err" }, el("span", { text: line }), " ", retryButton(retry || show)));
  }
  function emptyRow(tbody, cols, data) {
    var msg = data.last_computed ? fmt(W.noItems, { t: data.last_computed_label }) : W.noDataYet;
    tbody.appendChild(el("tr", null, el("td", { cls: "empty", colspan: String(cols), text: msg })));
  }
  function setStamp(node, data) {
    node.textContent = data.last_computed ? fmt(W.lastComputed, { t: data.last_computed_label }) : W.noDataYet;
    node.classList.toggle("stale", !!data.stale);
  }
  function stampAll(view, data) { view.querySelectorAll("[data-stamp]").forEach(function (n) { setStamp(n, data); }); }
  function tabs(box, items, current, onPick) {
    clear(box);
    items.forEach(function (it) {
      box.appendChild(el("button", { type: "button", cls: it[0] === current ? "on" : "", text: it[1], on: { click: function () { onPick(it[0]); } } }));
    });
  }
  function pager(box, total, offset, onMove) {
    clear(box);
    if (total <= PAGE) { box.appendChild(el("span", { cls: "stamp", text: fmt(W.shown, { n: total }) })); return; }
    box.appendChild(el("span", { cls: "stamp", text: fmt(W.range, { a: offset + 1, b: Math.min(offset + PAGE, total), n: total }) }));
    if (offset > 0) box.appendChild(el("button", { cls: "btn", type: "button", text: W.previous, on: { click: function () { onMove(offset - PAGE); } } }));
    if (offset + PAGE < total) box.appendChild(el("button", { cls: "btn", type: "button", text: W.next, on: { click: function () { onMove(offset + PAGE); } } }));
  }
  function fillWords() {
    document.querySelectorAll("[data-w]").forEach(function (n) { n.textContent = W[n.getAttribute("data-w")] || ""; });
    $("gsearch").placeholder = W.searchAll;
    document.querySelectorAll(".vsearch").forEach(function (n) { n.placeholder = W.searchPage; });
    $("changed-text").textContent = W.changed;
    $("changed-reload").textContent = W.reload;
    $("status-retry").textContent = W.retryBtn;
  }

  // ---------- routing ----------
  function parseHash() {
    var h = (location.hash || "#today").slice(1);
    var parts = h.split("?");
    var params = {};
    (parts[1] || "").split("&").forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf("=");
      try {
        if (i < 0) params[decodeURIComponent(kv)] = "";
        else params[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1));
      } catch (e) { /* a bad link: skip that part */ }
    });
    return { view: parts[0] || "today", params: params };
  }
  function go(view, params) {
    var qs = params ? Object.keys(params).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); }).join("&") : "";
    location.hash = view + (qs ? "?" + qs : "");
  }
  function show() {
    var r = parseHash();
    var view = $("v-" + r.view) ? r.view : "today";
    if (view === "sources" && state.role !== "admin") view = "today";
    closeDrawer();
    document.querySelectorAll(".view").forEach(function (s) { s.classList.toggle("on", s.id === "v-" + view); });
    document.querySelectorAll(".rail a[data-v]").forEach(function (a) { a.classList.toggle("on", a.dataset.v === view || (view === "money" && a.dataset.v === "blockers")); });
    var loader = LOADERS[view];
    if (loader) loader(r.params);
    window.scrollTo(0, 0);
  }

  // ---------- top bar ----------
  function loadMe() {
    return api("/api/me").then(function (me) {
      state.people = me.people;
      state.role = me.role;
      $("me").textContent = fmt(W.signedIn, { email: me.email });
      var out = $("signout");
      out.textContent = W.signOut;
      out.onclick = function () {
        fetch("/auth/signout", { method: "POST", credentials: "same-origin" }).then(function () { location.href = "/signin"; });
      };
      $("nav-sources").hidden = me.role !== "admin";
    });
  }
  function loadStatus() {
    clearTimeout(state.statusRetry);
    api("/api/status").then(function (s) {
      $("status-retry").hidden = true;
      var node = $("updated");
      node.textContent = s.message;
      node.className = "status " + (s.state === "ok" ? "ok" : "bad");
      $("cnt-blockers").textContent = s.late_total || "";
      // The line comes from the server (words.py): waiting, held (writes off) or failed. A
      // failed write is never hidden: the line stays until the write is fixed or undone.
      $("pending").hidden = !s.write_note;
      $("pending").textContent = s.write_note || "";
      $("pending").className = "status " + (s.write_bad ? "bad" : "pending");
      clearTimeout(state.pendingTimer);
      if (s.pending_writes) state.pendingTimer = setTimeout(loadStatus, PENDING_POLL_MS);
      if (UI.onStatus) UI.onStatus(s);
    }).catch(function (e) {
      $("updated").textContent = loadError(W.whatStatus, e);
      $("updated").className = "status bad";
      $("status-retry").hidden = false;
      if (e.network) state.statusRetry = setTimeout(loadStatus, STATUS_RETRY_MS);
    });
  }

  // ---------- views ----------
  function tileNode(t) {
    var href = "#" + t.link.view + (t.link.filter ? "?filter=" + encodeURIComponent(t.link.filter) : "");
    return el("a", { cls: "tile" + (t.value == null ? " unknown" : ""), href: href, "data-tile": t.key, title: fmt(W.sourceTip, { s: t.source }) },
      el("div", { cls: "l", text: t.label }), el("div", { cls: "v", text: num(t.value) }), el("div", { cls: "d", text: t.detail }));
  }
  function loadToday() {
    var box = $("tiles");
    api("/api/today").then(function (d) {  // D207: the signed-in person's own list
      $("hello").textContent = d.greeting;
      $("today-sub1").textContent = fmt(W.todaySub1, { date: d.date_label });
      $("today-sub2").textContent = W.todaySub2;
      stampAll($("v-today"), d);
      clear(box);
      d.tiles.forEach(function (t) { box.appendChild(tileNode(t)); });
      if (UI.renderTasks) UI.renderTasks(d.my_tasks);
      if (UI.renderLook) UI.renderLook(d.needs_look);
      if (UI.renderSuggests) UI.renderSuggests(d.saley_suggests);
    }).catch(function (e) { fail(box, e); });
  }

  function lateText(days) {
    if (days == null) return ["—", "gray"];
    if (days > 0) return [days === 1 ? W.dayLate : fmt(W.daysLate, { n: days }), days > 14 ? "bad" : "warn"];
    if (days === 0) return [W.dueToday, "warn"];
    return [fmt(W.dueIn, { n: -days }), "gray"];
  }
  function moreToggle(wrap, hidden) {
    var label = fmt(W.more, { n: hidden });
    var btn = el("button", { cls: "link", type: "button", text: label });
    btn.addEventListener("click", function () { var open = wrap.classList.toggle("open"); btn.textContent = open ? W.showLess : label; });
    return btn;
  }
  function blockerList(blockers) {
    var wrap = el("div", { cls: "lines" });
    if (!blockers.length) { wrap.appendChild(el("span", { cls: "muted", text: W.blockersNone })); return wrap; }
    blockers.forEach(function (b, i) {
      var late = lateText(b.late_by_days);
      wrap.appendChild(el("div", { cls: "line-item" + (i >= BLOCKERS_SHOWN ? " extra" : "") },
        el("span", { cls: "name", title: b.item, text: b.short }), " · ", el("span", { cls: "muted", text: b.owner || "—" }), " ", pill(late[0], late[1])));
    });
    if (blockers.length > BLOCKERS_SHOWN) wrap.appendChild(moreToggle(wrap, blockers.length - BLOCKERS_SHOWN));
    return wrap;
  }
  function activeList(g) {
    var wrap = el("div", { cls: "lines" });
    if (!g.active.length) { wrap.appendChild(el("span", { cls: "muted", text: W.activeNone })); return wrap; }
    g.active.forEach(function (a) {
      wrap.appendChild(el("div", { cls: "line-item" }, el("span", { cls: "name", text: a.name }), " · ", el("span", { cls: "muted", text: a.company || "—" }), " ", stagePill(a.stage)));
    });
    if (g.active_count > g.active.length) wrap.appendChild(el("div", { cls: "muted", text: fmt(W.activeMore, { n: g.active_count - g.active.length }) }));
    return wrap;
  }
  function goalCard(g) {
    var weighted = g.weighted_usd != null ? "≈ " + usd(g.weighted_usd) : g.weighted_note;
    var table = el("table", { cls: "narrow goal-table" },
      el("tr", null, el("th", { text: W.activeTitle }), el("td", null, activeList(g))),
      el("tr", null, el("th", { text: W.needTitle }), el("td", { text: g.target || "—" })),
      el("tr", null, el("th", { text: W.blockersTitle }), el("td", null, blockerList(g.blockers))),
      el("tr", null, el("th", { text: W.weightedTitle }), el("td", { text: weighted })));
    return el("div", { cls: "card goal", "data-goal": g.code },
      el("div", { cls: "goal-head" },
        el("span", { cls: "muted", text: g.code + " · " + (g.value_text || "—") + " · " + fmt(W.goalDue, { d: g.due_label }) }),
        el("div", { cls: "rag" }, pill(g.rag, RAG_PILL[g.rag] || "gray"), el("div", { cls: "why-line", text: g.why }))),
      el("h2", { cls: "first" }, longText(g.text)), el("div", { cls: "tw" }, table));
  }
  function loadGoals() {
    var box = $("goals");
    api("/api/goals").then(function (d) {
      stampAll($("v-goals"), d);
      clear(box);
      if (!d.items.length) { box.appendChild(el("div", { cls: "empty", text: fmt(W.noGoals, { t: d.last_computed_label }) })); return; }
      d.items.forEach(function (g) { box.appendChild(goalCard(g)); });
    }).catch(function (e) { fail(box, e); });
  }

  function rowOpen(fn) { return { click: fn, keydown: function (e) { if (e.key === "Enter") fn(); } }; }
  function accountRow(a) {
    var goals = a.goal_codes.length ? a.goal_codes.join(", ") : "—";
    var bar = el("div", { cls: "bar", title: num(a.coverage_pct) + " %" }, el("i"));
    bar.firstChild.style.width = (a.coverage_pct || 0) + "%";
    var group = a.threads >= 2 ? pill(fmt(W.groupEngaged, { n: a.threads }), "ok") : a.threads === 1 ? pill(W.groupSingle, "warn") : pill(W.groupNone, "gray");
    var name = el("td", null, el("b", { text: a.name }), a.p1_count ? el("span", null, " ", pill("P1 ×" + a.p1_count, "warn")) : null);
    return el("tr", { cls: "click", tabindex: "0", on: rowOpen(function () { openAccount(a.id); }) }, name,
      el("td", { text: a.industry || "—" }), el("td", { text: goals, title: W.industryRule }),
      el("td", { cls: "num", text: a.people }), el("td", { cls: "num", text: a.contacted }), el("td", { cls: "num", text: a.connected }), el("td", { cls: "num", text: a.emailed }),
      el("td", null, bar), el("td", null, stagePill(a.warmth)), el("td", null, group));
  }
  function loadAccounts(params) {
    if (params && params.filter) state.accFilter = params.filter;
    var q = $("acc-search").value;
    var tbody = $("acc-table").querySelector("tbody");
    tabs($("acc-tabs"), [["all", W.tabAll], ["p1", W.tabP1], ["has_connection", W.tabConnected], ["goal:G3", W.tabG3], ["goal:G5", W.tabG5]], state.accFilter, function (f) { state.accFilter = f; state.accOffset = 0; loadAccounts(); });
    api("/api/accounts?filter=" + encodeURIComponent(state.accFilter) + "&limit=" + PAGE + "&offset=" + state.accOffset + (q ? "&q=" + encodeURIComponent(q) : "")).then(function (d) {
      $("acc-stamp").textContent = fmt(W.accountsStamp, { n: d.with_people, m: d.in_master });
      $("acc-stamp").classList.toggle("stale", !!d.stale);
      clear(tbody);
      if (!d.items.length) emptyRow(tbody, 10, d);
      d.items.forEach(function (a) { tbody.appendChild(accountRow(a)); });
      pager($("acc-pager"), d.total, state.accOffset, function (o) { state.accOffset = o; loadAccounts(); });
      var ind = clear($("acc-industries"));
      d.industries.forEach(function (i) {
        var bar = el("div", { cls: "bar" }, el("i"));
        bar.firstChild.style.width = (i.pct || 0) + "%";
        ind.appendChild(el("div", { cls: "row" }, el("span", { text: i.industry + " (" + i.people + ")" }), bar, el("span", { cls: "n", text: num(i.pct) + " %" })));
      });
    }).catch(function (e) { fail(tbody, e); });
  }

  function touchesText(list) {
    if (!list || !list.length) return "—";
    return list.map(function (t) { return (TOUCH_WORDS[t.type] || t.type) + " " + dayLabel(t.date); }).join(" · ");
  }
  function personRow(p) {
    return el("tr", { cls: "click", tabindex: "0", on: rowOpen(function () { openPerson(p.id); }) },
      el("td", null, el("b", { text: p.name }), p.is_p1 ? el("span", null, " ", pill("P1", "warn")) : null),
      el("td", { text: (p.company || "—") + (p.title ? " · " + p.title : "") }),
      el("td", null, stagePill(p.stage)),
      el("td", { title: p.about ? W.aboutSource : "" }, p.about ? longText(p.about) : "—"),
      el("td", null, p.has_email ? pill(W.yes, "ok") : pill(W.no, "gray")),
      el("td", null, p.next_step ? longText(p.next_step) : "—"),
      el("td", { text: touchesText(p.last_touches) }));
  }
  function loadPeople(params) {
    if (params && params.filter) { state.peopleFilter = params.filter; state.pplOffset = 0; }
    var q = $("ppl-search").value;
    var tbody = $("ppl-table").querySelector("tbody");
    api("/api/people?filter=" + encodeURIComponent(state.peopleFilter) + "&limit=" + PAGE + "&offset=" + state.pplOffset + (q ? "&q=" + encodeURIComponent(q) : "")).then(function (d) {
      var c = d.counts;
      $("ppl-stamp").textContent = fmt(W.peopleStamp, { n: c.people, m: c.with_notes, p: c.with_paper });
      $("ppl-stamp").classList.toggle("stale", !!d.stale);
      var labels = PEOPLE_TABS.map(function (t) { var n = c[t[0]]; return [t[0], t[1] + (n != null ? " (" + n + ")" : "")]; });
      if (!PEOPLE_TABS.some(function (t) { return t[0] === state.peopleFilter; })) labels.push([state.peopleFilter, fmt(W.fStage, { s: state.peopleFilter.replace(/^stage:/, "") })]);
      tabs($("ppl-tabs"), labels, state.peopleFilter, function (f) { go("people", { filter: f }); });
      clear(tbody);
      if (!d.items.length) emptyRow(tbody, 7, d);
      d.items.forEach(function (p) { tbody.appendChild(personRow(p)); });
      pager($("ppl-pager"), d.total, state.pplOffset, function (o) { state.pplOffset = o; loadPeople(); });
    }).catch(function (e) { fail(tbody, e); });
  }

  function blocksText(b) {
    var parts = [];
    if (b.blocks_goals.length) parts.push(b.blocks_goals.length >= 5 ? fmt(W.allGoals, { n: b.blocks_goals.length }) : fmt(W.goalsList, { g: b.blocks_goals.join(", ") }));
    if (b.blocks_motions.length) parts.push(b.blocks_motions.length > 1 ? fmt(W.motions, { n: b.blocks_motions.length }) : W.motion);
    return parts.join(" · ") || "—";
  }
  function loadBlockers() {
    var tbody = $("blk-table").querySelector("tbody"), ptbody = $("pkg-table").querySelector("tbody");
    api("/api/blockers").then(function (d) {
      stampAll($("v-blockers"), d);
      $("cnt-blockers").textContent = d.late_total || "";
      clear($("money-tile")).appendChild(tileNode(d.money_tile));
      clear(tbody); clear(ptbody);
      if (!d.late.length) emptyRow(tbody, 6, d);
      d.late.forEach(function (b) {
        var late = lateText(b.late_by_days);
        var low = (b.status || "").toLowerCase();
        tbody.appendChild(el("tr", { title: fmt(W.dSheetRow, { r: b.evidence.row || "—" }) },
          el("td", { title: b.item, text: b.short }), el("td", { text: b.owner || "—" }), el("td", { text: b.due_label }),
          el("td", { cls: "num" }, pill(late[0], late[1])), el("td", { text: blocksText(b) }),
          el("td", null, pill(b.status || W.noStatus, low.indexOf("progress") >= 0 || low.indexOf("review") >= 0 ? "warn" : "bad"))));
      });
      if (!d.packages.length) emptyRow(ptbody, 4, d);
      d.packages.forEach(function (p) {
        ptbody.appendChild(el("tr", null, el("td", { text: p.name }), el("td", null, p.purpose ? longText(p.purpose) : "—"), el("td", { cls: "num", text: p.pct == null ? "—" : p.pct + " %" }), el("td", { text: p.status || "—" })));
      });
    }).catch(function (e) { fail(tbody, e); });
  }

  function loadMoney() {
    var tbody = $("money-table").querySelector("tbody");
    api("/api/money-blocked").then(function (d) {
      stampAll($("v-money"), d);
      var known = d.total - d.no_value;
      $("money-total").textContent = fmt(W.moneyTotal, { v: known > 0 || !d.total ? usd(d.total_usd) : "—" });
      $("money-novalue").textContent = d.no_value ? fmt(W.moneyNoValue, { n: d.no_value }) : "";
      clear(tbody);
      if (!d.items.length) emptyRow(tbody, 5, d);
      d.items.forEach(function (m) {
        var item = el("td", null, el("span", { title: m.deliverable, text: m.short }), m.more_blocking ? el("span", { cls: "muted", text: " " + fmt(W.moneyMore, { n: m.more_blocking }) }) : null);
        tbody.appendChild(el("tr", { cls: "click", tabindex: "0", on: rowOpen(function () { openPerson(m.contact_id); }) },
          el("td", null, el("b", { text: m.name }), " ", stagePill(m.stage)), el("td", { text: m.company || "—" }),
          el("td", { cls: "num" + (m.size_usd == null ? " muted" : ""), text: m.value_label }), item, el("td", { text: m.owner || "—" })));
      });
    }).catch(function (e) { fail(tbody, e); });
  }

  // R4 (D118): the Scorecard "last week" cell. A null week or a null part shows a dash, never 0.
  function lastWeekCell(r) {
    var lw = r.last_week;
    if (!lw) {
      var cell = el("td", { cls: "muted", text: "—" });
      if (r.last_week_note) cell.appendChild(el("span", { cls: "stamp", text: " " + r.last_week_note }));
      return cell;
    }
    return el("td", { text: fmt(W.scLastWeekLine, { c: num(lw.contacted), m: num(lw.meetings), d: num(lw.deals) }) });
  }
  function scoreRow(r) {
    var reply = r.reply_pct == null ? el("td", { cls: "num muted", title: r.reply_note, text: "—" }) : el("td", { cls: "num", text: r.reply_pct });
    return el("tr", null, el("td", { text: r.industry }), el("td", { cls: "num", text: r.people }), el("td", { cls: "num", text: r.contacted }),
      el("td", { cls: "num", text: num(r.connect_pct) }), reply, el("td", { cls: "num", text: r.meetings }), el("td", { cls: "num", text: r.deals }), lastWeekCell(r));
  }
  function loadScorecard() {
    var tbody = $("sc-table").querySelector("tbody");
    api("/api/scorecard").then(function (d) {
      stampAll($("v-scorecard"), d);
      clear(tbody);
      if (!d.items.length) emptyRow(tbody, 8, d);
      d.items.forEach(function (r) { tbody.appendChild(scoreRow(r)); });
    }).catch(function (e) { fail(tbody, e); });
  }

  function renderTabs(d) {
    var body = clear($("tabs-table").querySelector("tbody"));
    d.tabs.forEach(function (t) {
      var ok = t.status === "ok";
      var why = ok ? "" : [t.missing_headers.length ? fmt(W.missing, { h: t.missing_headers.join(", ") }) : t.reason, fmt(W.tabStale, { t: timeLabel(t.stale_since) })].filter(Boolean).join(" · ");
      body.appendChild(el("tr", { cls: ok ? "" : "red-line" }, el("td", { text: t.title || t.tab }), el("td", null, pill(ok ? W.tabOk : t.status, ok ? "ok" : "bad")), el("td", { text: why })));
    });
    var cols = clear($("new-columns"));
    var fresh = d.tabs.filter(function (t) { return t.new_headers.length; });
    if (!fresh.length) cols.appendChild(el("p", { cls: "muted", text: W.newColumnsNone }));
    fresh.forEach(function (t) { cols.appendChild(el("p", null, el("b", { text: (t.title || t.tab) + ": " }), t.new_headers.join(", "))); });
  }
  function renderJobs(d) {
    var jobs = clear($("jobs-table").querySelector("tbody"));
    if (!d.jobs.length) emptyRow(jobs, 4, d);
    d.jobs.forEach(function (j) {
      var cls = j.status === "ok" ? "ok" : j.status === "failed" ? "bad" : "warn";
      jobs.appendChild(el("tr", null, el("td", { text: j.job }), el("td", { text: timeLabel(j.finished_at || j.started_at) }), el("td", null, pill(j.status, cls)), el("td", null, longText(j.note || ""))));
    });
    var llmCls = d.llm.calls >= d.llm_cap ? "bad" : d.llm.calls >= 0.8 * d.llm_cap ? "warn" : "ok";
    jobs.appendChild(el("tr", null, el("td", { text: "llm" }), el("td", { text: "—" }), el("td", null, pill(d.llm.calls + " / " + d.llm_cap, llmCls)), el("td", { text: fmt(W.healthLlm, { n: d.llm.cached }) })));
  }
  function renderChecks(d) {
    var parity = clear($("parity-table").querySelector("tbody"));
    if (!d.parity.length) emptyRow(parity, 4, d);
    d.parity.forEach(function (p) {
      var verdict = p.note === "skipped: tab stale" ? pill(W.skipped, "gray") : p.ok ? pill(W.match, "ok") : pill(W.differs, "bad");
      parity.appendChild(el("tr", null, el("td", { text: p.metric.replace("stage:", "Stage ").replace(/_/g, " ") }), el("td", { cls: "num", text: num(p.sheet_value) }), el("td", { cls: "num", text: num(p.view_value) }), el("td", null, verdict)));
    });
    var dq = clear($("dq-table").querySelector("tbody"));
    dq.appendChild(el("tr", null, el("td", { text: W.noIndustry }), el("td", { cls: "num", text: d.missing_industry })));
    d.quality.forEach(function (q) { dq.appendChild(el("tr", null, el("td", { text: q.problem.replace(/_/g, " ") }), el("td", { cls: "num", text: q.n }))); });
    $("news-note").textContent = d.news_note;
    var eg = clear($("egress-table").querySelector("tbody"));
    if (!d.egress.length) emptyRow(eg, 3, d);
    d.egress.forEach(function (e) { eg.appendChild(el("tr", null, el("td", { text: e.host }), el("td", { cls: "num", text: e.calls }), el("td", { cls: "num", text: e.errors }))); });
    $("llm-line").textContent = fmt(W.sheetLine, { m: d.sheet_mode, n: d.llm.calls, c: d.llm_cap });
  }
  function loadSources(params) {
    api("/api/health/full").then(function (d) {
      stampAll($("v-sources"), d);
      renderTabs(d); renderJobs(d); renderChecks(d);
      if (UI.renderNews) UI.renderNews(d, params || {});
    }).catch(function (e) { fail($("jobs-table").querySelector("tbody"), e); });
  }

  function hitOpen(h) {
    if (h.type === "person") return function () { openPerson(h.id); };
    if (h.type === "company") return function () { openAccount(h.id); };
    if (h.type === "goal") return function () { go("goals"); };
    if (h.type === "deliverable") return function () { go("blockers"); };
    return null;
  }
  function hitRow(h) {
    var open = hitOpen(h);
    var name = open ? el("button", { cls: "link hit-name", type: "button", on: { click: open } }, marked(h.name, h.match)) : el("b", null, marked(h.name, h.match));
    return el("div", { cls: "hit" }, name,
      h.pill ? el("span", null, " ", pill(h.pill, STAGE_PILL[h.pill] || RAG_PILL[h.pill] || "gray")) : null,
      el("div", { cls: "muted ctx", text: h.context + (h.owner ? " · " + ownerName(h.owner) : "") }));
  }
  function loadSearch(params) {
    var q = params.q || "";
    var box = clear($("search-results"));
    $("gsearch").value = q;
    $("search-title").textContent = q ? fmt(W.searchTitleQ, { q: q }) : W.searchTitle;
    $("search-sub").textContent = q ? "" : W.searchHint;
    if (!q) return;
    api("/api/search?q=" + encodeURIComponent(q)).then(function (d) {
      if (!d.total) {
        box.appendChild(el("div", { cls: "card empty-state" }, el("h2", { cls: "first", text: fmt(W.zeroResults, { q: q }) }), el("p", { cls: "muted", text: W.zeroHint })));
        return;
      }
      var grid = el("div", { cls: "grid2" });
      GROUPS.forEach(function (g) {
        var group = d.groups[g[0]];
        if (!group || !group.total) return;  // only groups with a match; the zero case is above
        var card = el("div", { cls: "card", "data-group": g[0] }, el("h2", { cls: "first" }, g[1] + " ", el("span", { cls: "hint", text: "(" + group.total + ")" })));
        group.items.forEach(function (h) { card.appendChild(hitRow(h)); });
        if (group.total > group.items.length) card.appendChild(el("p", { cls: "muted", text: fmt(W.gMore, { n: group.total - group.items.length }) }));
        grid.appendChild(card);
      });
      box.appendChild(grid);
    }).catch(function (e) { fail(box, e); });
  }

  // ---------- drawers ----------
  function openDrawer(build) {
    var d = clear($("drawer"));
    d.appendChild(el("button", { cls: "btn close", type: "button", text: W.close, on: { click: closeDrawer } }));
    d.appendChild(el("div", { cls: "empty", text: W.loading }));
    d.classList.add("on"); $("drawer-back").classList.add("on");
    build(d);
  }
  function closeDrawer() { $("drawer").classList.remove("on"); $("drawer-back").classList.remove("on"); }
  function fields(pairs) {
    var dl = el("dl");
    pairs.forEach(function (p) {
      dl.appendChild(el("dt", { text: p[0] }));
      var v = p[1];
      dl.appendChild(el("dd", null, v == null || v === "" ? noneOnFile() : typeof v === "object" ? v : longText(v)));
    });
    return dl;
  }
  function historyTable(rows, withName) {
    if (!rows.length) return el("div", { cls: "empty muted", text: W.noneOnFile });
    var t = el("table", { cls: "narrow" });
    rows.forEach(function (r) {
      t.appendChild(el("tr", null, withName ? el("td", { text: r.name }) : null, el("td", { text: r.stage || TOUCH_WORDS[r.type] || r.type }),
        el("td", { text: r.from_at ? timeLabel(r.from_at) : dayLabel(r.date) }), el("td", { text: r.reason || r.outcome || "" })));
    });
    return el("div", { cls: "tw" }, t);
  }
  function personFields(p) {
    var o = p.opportunity;
    var deal = o ? [o.status, o.deal_status, o.prob != null ? o.prob + " %" : null, o.size_text].filter(Boolean).join(" · ") : null;
    return fields([
      [W.dStageReason, p.stage_history[0] && p.stage_history[0].reason],
      [W.dOwner, p.owner ? fmt(W.dOwnerFrom, { o: ownerName(p.owner), s: OWNER_SOURCE[p.owner_source] || W.ownerSrcDefault }) : null],
      [W.dPriority, p.priority], [W.dCity, p.city], [W.dIndustry, p.industry],
      [W.dEmail, p.email_href ? el("a", { href: p.email_href, text: p.email }) : null],
      [W.dLinkedIn, p.li_href ? link(p.li_href, p.li_href.replace(/^https:\/\/(www\.)?/, "")) : null],
      [W.dWebsite, p.site_href ? link(p.site_href, p.site_text) : p.site_text],
      [W.dPaper, p.paper_href ? link(p.paper_href, p.paper_href.replace(/^https:\/\//, "")) : null],
      [W.dLiStatus, p.li_status], [W.dNextStep, p.next_step],
      [W.dWorkingOn, p.about], [W.dNotes, p.notes], [W.dDeal, deal || null],
      [W.dEvidence, p.evidence.row ? fmt(W.dSheetRow, { r: p.evidence.row }) : null]]);
  }
  function openPerson(id) {
    openDrawer(function (d) {
      api("/api/people/" + id).then(function (r) {
        var p = r.item; d.removeChild(d.lastChild);
        d.appendChild(el("h1", { text: p.name }));
        d.appendChild(el("p", { cls: "sub" }, (p.title || "") + (p.company ? " · " + p.company : "") + " ", stagePill(p.stage)));
        d.appendChild(personFields(p));
        d.appendChild(el("h2", { text: W.dTouches })); d.appendChild(historyTable(p.touches, false));
        d.appendChild(el("h2", { text: W.dHistory })); d.appendChild(historyTable(p.stage_history, false));
      }).catch(function (e) { fail(d, e, W.whatItem, function () { openPerson(id); }); });
    });
  }
  function openAccount(id) {
    openDrawer(function (d) {
      api("/api/accounts/" + id).then(function (r) {
        var a = r.item; d.removeChild(d.lastChild);
        d.appendChild(el("h1", { text: a.name }));
        d.appendChild(el("p", { cls: "sub" }, (a.industry || "—") + " ", stagePill(a.warmth)));
        d.appendChild(el("p", { cls: "muted", text: fmt(W.dCounts, { n: a.people, c: a.contacted, k: a.connected }) }));
        d.appendChild(fields([[W.dGeo, a.geo], [W.dFunding, a.funding], [W.dGoal, a.goal_codes.join(", ")],
          [W.dInMaster, a.in_master ? W.yes : W.dInMasterNo], [W.dEvidence, a.evidence.row ? fmt(W.dSheetRow, { r: a.evidence.row }) : null]]));
        d.appendChild(el("h2", { text: W.dPeople }));
        var t = el("table", { cls: "narrow" });
        a.people_list.forEach(function (p) { t.appendChild(el("tr", { cls: "click", tabindex: "0", on: rowOpen(function () { openPerson(p.id); }) }, el("td", { text: p.name }), el("td", { text: p.title || "" }), el("td", null, stagePill(p.stage)))); });
        d.appendChild(el("div", { cls: "tw" }, t));
        d.appendChild(el("h2", { text: W.dTouches })); d.appendChild(historyTable(a.touches, true));
        d.appendChild(el("h2", { text: W.dHistory })); d.appendChild(historyTable(a.stage_history, true));
      }).catch(function (e) { fail(d, e, W.whatItem, function () { openAccount(id); }); });
    });
  }

  // ---------- page search: filters what is on screen; People and Accounts also ask the API ----------
  function pageSearch(input) {
    var view = input.closest(".view");
    var q = input.value.toLowerCase();
    view.querySelectorAll("tbody tr, #goals > .card").forEach(function (r) { r.style.display = !q || r.textContent.toLowerCase().indexOf(q) >= 0 ? "" : "none"; });
  }

  var LOADERS = { today: loadToday, goals: loadGoals, accounts: loadAccounts, people: loadPeople, blockers: loadBlockers, money: loadMoney, scorecard: loadScorecard, sources: loadSources, search: loadSearch };
  // What act.js and team.js need from this file. They add their loaders before the page starts.
  UI.el = el; UI.clear = clear; UI.$ = $; UI.api = api; UI.fail = fail; UI.state = state; UI.pill = pill;
  UI.tabs = tabs; UI.dayLabel = dayLabel; UI.timeLabel = timeLabel; UI.loaders = LOADERS;
  UI.loadStatus = loadStatus; UI.loadToday = loadToday;
  UI.pager = pager; UI.PAGE = PAGE; UI.num = num; UI.link = link; UI.go = go; UI.emptyRow = emptyRow;
  UI.setStamp = setStamp; UI.scoreRow = scoreRow; UI.openPerson = openPerson; UI.openAccount = openAccount; UI.stagePill = stagePill;

  document.addEventListener("DOMContentLoaded", function () {
    fillWords();
    document.querySelectorAll(".vsearch").forEach(function (inp) {
      if (inp.id === "ppl-search") inp.addEventListener("input", debounce(function () { state.pplOffset = 0; loadPeople(); }, 300));
      else if (inp.id === "acc-search") inp.addEventListener("input", debounce(function () { state.accOffset = 0; loadAccounts(); }, 300));
      else inp.addEventListener("input", function () { pageSearch(inp); });
    });
    $("gsearch").addEventListener("keydown", function (e) { if (e.key === "Enter" && $("gsearch").value.trim()) go("search", { q: $("gsearch").value.trim() }); });
    $("changed-reload").addEventListener("click", function () { location.reload(); });
    $("drawer-back").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDrawer(); });
    window.addEventListener("hashchange", show);
    $("status-retry").addEventListener("click", loadStatus);
    loadMe().catch(function (e) { $("me").textContent = loadError(W.whatMe, e); }).then(function () { show(); loadStatus(); });
    setInterval(loadStatus, STATUS_POLL_MS);
  });
})();
