// Saley dashboard, R3: Signals, Events, Suggestions, the two new Today sections, and the news
// block of Sources and health. Plain JS, no build step (docs/08 "Stack").
// Every value from the API goes into the page with textContent (el() does that), never as HTML:
// a headline is data typed by a stranger. Every visible word comes from words.js or from the API.
// Seen (D75): after a list is drawn, the page tells Saley which new items it showed
// (UI.markSeen). The next visit shows unseen items first and says how many it left out.
(function () {
  "use strict";

  var UI = window.SaleyUI;
  var W = window.WORDS;
  var fmt = window.fmt;
  var el = UI.el;
  var $ = UI.$;
  var clear = UI.clear;
  var pill = UI.pill;

  var SIG_RANGES = [["day", W.rangeDay], ["week", W.rangeWeek], ["month", W.rangeMonth], ["year", W.rangeYear]];
  var SORTS = [["rank", W.sortRank], ["newest", W.sortNewest], ["importance", W.sortImportance]];
  var TAGS = ["Opportunity", "People", "Risk", "Market", "Tender", "Discover"];
  var CATEGORIES = ["Hiring", "Funding", "Product launch", "Research", "Partnership", "Event", "Other"]; // D156: names, never letters
  var SUGGEST_TABS = [["warm", W.sgTabWarm], ["best", W.sgTabBest], ["newco", W.sgTabNewco], ["res", W.sgTabRes]];
  var SUGGEST_SEEN = { warm: "suggest_warm", best: "suggest_best", newco: "suggest_newco", res: "suggest_res" };
  var EVENT_TABS = [["upcoming", W.evTabUp], ["past", W.evTabPast]];
  var OUTLETS_SHOWN = 3;
  var TODAY_PARTS = 3;
  var FILTER_MS = 300;
  var FUNNEL_PILL_COLUMN = { tried: 2, answered: 2, fetched: 4, deduped: 5 };

  var sig = { tab: "all", range: "week", sort: "rank", cat: "", tag: "", src: "", entity: "", offset: 0, includeSeen: false, day: "", minImportance: "", linkedOnly: false, req: 0, items: [], data: null };
  var sug = { kind: "warm", offset: 0, includeSeen: false, req: 0 };
  var evt = { when: "upcoming", includeSeen: false, req: 0 };

  // ---------- shared small parts ----------
  function newsStamp(node, d) {
    node.textContent = d.last_computed ? fmt(W.lastComputed, { t: d.last_computed_label }) : W.noNewsYet;
    node.classList.toggle("stale", !!d.stale);
  }
  function select(id, options, current, onPick) {
    var box = clear($(id));
    options.forEach(function (o) { box.appendChild(el("option", { value: o[0], text: o[1] })); });
    box.value = current;
    box.onchange = function () { onPick(box.value); };
  }
  function ids(items) { return items.filter(function (i) { return !i.seen; }).map(function (i) { return i.id; }); }
  // The note under a list: "N you saw are left out. Show them." or a way back.
  function leftOut(box, d, items, includeSeen, toggle) {
    clear(box);
    var anySeen = items.some(function (i) { return i.seen; });
    if (includeSeen && anySeen) {
      box.appendChild(el("button", { cls: "link", type: "button", "data-left": "hide", text: W.hideSeen, on: { click: function () { toggle(false); } } }));
    } else if (!includeSeen && d.left_out_seen > 0) {
      box.appendChild(el("button", { cls: "link", type: "button", "data-left": "show", text: d.left_out_note, on: { click: function () { toggle(true); } } }));
    }
  }
  function unseenFirst(items) {
    return items.map(function (it, i) { return [it, i]; }).sort(function (a, b) {
      return (a[0].seen === b[0].seen ? 0 : a[0].seen ? 1 : -1) || a[1] - b[1];
    }).map(function (p) { return p[0]; });
  }
  function headline(it) {
    return it.href ? UI.link(it.href, it.title) : document.createTextNode(it.title);
  }
  function emptyItem(text) { return el("li", { cls: "empty", text: text }); }

  // ---------- Signals ----------
  function sortedItems(items) {
    var list = items.slice();
    var when = function (i) { return i.published_at || ""; };
    if (sig.sort === "newest") list.sort(function (a, b) { return when(b) < when(a) ? -1 : when(b) > when(a) ? 1 : 0; });
    if (sig.sort === "importance") list.sort(function (a, b) { return (b.importance - a.importance) || ((b.impact || 0) - (a.impact || 0)); });
    return unseenFirst(list);
  }
  function outletsText(it) {
    var shown = it.outlets.slice(0, OUTLETS_SHOWN).join(", ");
    var more = it.outlets.length - OUTLETS_SHOWN;
    return more > 0 ? shown + " " + fmt(W.outletsMore, { n: more }) : shown;
  }
  function metaLine(it) {
    var meta = el("div", { cls: "it-meta" });
    if (it.tag) meta.appendChild(pill(it.tag, "gray"));
    meta.appendChild(pill(fmt(W.impLabel, { n: it.importance == null ? "—" : it.importance }), it.importance >= 4 ? "warn" : "gray"));
    meta.appendChild(pill(fmt(W.impactLabel, { n: it.impact == null ? "—" : it.impact }), "gray"));
    if (it.ours) meta.appendChild(pill(W.oursPill, "acc"));
    if (it.verify) meta.appendChild(el("span", { title: it.verify_note || "" }, pill(W.verifyPill, "warn")));
    if (it.seen) meta.appendChild(pill(W.seenPill, "gray"));
    var from = outletsText(it) || it.source;
    if (from) meta.appendChild(el("span", { text: from }));
    if (it.source && it.source !== from) meta.appendChild(el("span", { text: it.source }));
    meta.appendChild(el("span", { text: it.published_label }));
    return meta;
  }
  function linkedLines(it, row) {
    var who = it.entity + (it.account && it.account !== it.entity ? " · " + it.account : "");
    row.appendChild(el("div", { cls: "it-line", "data-field": "entity" }, el("b", { text: fmt(W.linkedTo, { e: who }) })));
    row.appendChild(el("div", { cls: "it-line muted", "data-field": "hint", text: fmt(W.hintLine, { h: it.action_hint }) }));
    if (it.state && it.state !== "new") row.appendChild(el("div", { cls: "it-line muted", text: fmt(W.stateLabel, { s: it.state }) }));
  }
  function signalRow(it, linked) {
    var row = el("li", { "data-id": it.id, "data-seen": it.seen ? "1" : "0", cls: it.seen ? "is-seen" : "" });
    row.appendChild(el("div", { cls: "it-head" }, el("span", { cls: "it-title" }, headline(it))));
    if (linked) linkedLines(it, row);
    if (it.why) row.appendChild(el("div", { cls: "it-line muted", text: it.why }));
    row.appendChild(metaLine(it));
    return row;
  }
  function sigQuery() {
    var q = ["tab=" + sig.tab, "range=" + sig.range, "limit=" + UI.PAGE, "offset=" + sig.offset];
    [["day", sig.day], ["cat", sig.cat], ["tag", sig.tag], ["src", sig.src], ["entity", sig.entity], ["min_importance", sig.minImportance]].forEach(function (p) {
      if (p[1]) q.push(p[0] + "=" + encodeURIComponent(p[1]));
    });
    if (sig.linkedOnly) q.push("linked_only=true");
    if (sig.includeSeen) q.push("include_seen=true");
    return "/api/signals?" + q.join("&");
  }
  function drawControls() {
    UI.tabs($("sig-tabs"), [["all", W.sigTabAll], ["linked", W.sigTabLinked]], sig.tab, function (t) { sig.tab = t; sig.offset = 0; drawControls(); fetchSignals(); });
    UI.tabs($("sig-range"), SIG_RANGES, sig.day ? "" : sig.range, function (r) { sig.range = r; sig.day = ""; sig.offset = 0; drawControls(); fetchSignals(); });
    select("sig-sort", SORTS, sig.sort, function (v) { sig.sort = v; renderSignals(); });
    select("sig-cat", [["", W.catAny]].concat(CATEGORIES.map(function (c) { return [c, c]; })), sig.cat, function (v) { sig.cat = v; sig.offset = 0; fetchSignals(); });
    select("sig-tag", [["", W.tagAny]].concat(TAGS.map(function (t) { return [t, t]; })), sig.tag, function (v) { sig.tag = v; sig.offset = 0; fetchSignals(); });
    $("sig-src").value = sig.src;
    $("sig-entity").value = sig.entity;
    $("sig-entity-wrap").hidden = sig.tab !== "linked";
  }
  function renderSignals() {
    var d = sig.data;
    if (!d) return;
    var list = clear($("sig-list"));
    var items = sortedItems(d.items);
    if (!items.length) list.appendChild(emptyItem(d.last_computed ? fmt(W.noItems, { t: d.last_computed_label }) : W.noNewsYet));
    items.forEach(function (it) { list.appendChild(signalRow(it, d.tab === "linked")); });
    $("sig-window").textContent = fmt(W.winLine, { w: d.window.label }) + " · " + window.fmtCount(W.storiesCountOne, W.storiesCount, d.total);
    newsStamp($("sig-stamp"), d);
    leftOut($("sig-left"), d, d.items, sig.includeSeen, function (on) { sig.includeSeen = on; sig.offset = 0; fetchSignals(); });
    UI.pager($("sig-pager"), d.total, sig.offset, function (o) { sig.offset = o; fetchSignals(); });
  }
  function fetchSignals() {
    var mine = ++sig.req;
    UI.api(sigQuery()).then(function (d) {
      if (mine !== sig.req) return;
      sig.data = d;
      renderSignals();
      UI.markSeen(d.tab === "all" ? "story" : "signal", ids(d.items));  // after the list is drawn (D75)
    }).catch(function (e) { UI.fail($("sig-list"), e); });
  }
  function loadSignals(params) {
    var p = params || {};
    if (p.tab === "all" || p.tab === "linked") sig.tab = p.tab;
    if (p.range) sig.range = p.range;
    // A link from the news funnel names one day and a filter; leaving it clears them.
    sig.day = p.day || "";
    sig.minImportance = p.min_importance || "";
    sig.linkedOnly = p.linked_only === "true";
    sig.includeSeen = p.include_seen === "true";
    sig.offset = 0;
    $("sig-src").placeholder = W.srcPlaceholder;
    $("sig-entity").placeholder = W.entityPlaceholder;
    drawControls();
    fetchSignals();
  }

  // ---------- Events ----------
  function regText(v) { return v == null ? "—" : v ? W.yes : W.no; }
  function eventRow(e) {
    var name = e.href ? UI.link(e.href, e.name) : document.createTextNode(e.name);
    return el("tr", { "data-id": e.id, cls: e.seen ? "is-seen" : "" },
      el("td", null, name), el("td", { text: e.city || "—" }), el("td", { text: e.date_label }), el("td", { text: e.days_text }),
      el("td", { text: regText(e.registered) }), el("td", { cls: "num", text: e.people_in_city }), el("td", { cls: "num", text: e.connected }),
      el("td", { cls: "num", text: e.p1 }), el("td", { cls: "muted", text: e.speaker_matches }));
  }
  function loadEvents() {
    var tbody = $("ev-table").querySelector("tbody");
    UI.tabs($("ev-tabs"), EVENT_TABS, evt.when, function (w) { evt.when = w; evt.includeSeen = false; loadEvents(); });
    var mine = ++evt.req;
    UI.api("/api/events?when=" + evt.when + "&limit=" + UI.PAGE + (evt.includeSeen ? "&include_seen=true" : "")).then(function (d) {
      if (mine !== evt.req) return;
      clear(tbody);
      if (!d.items.length) UI.emptyRow(tbody, 9, d);
      unseenFirst(d.items).forEach(function (e) { tbody.appendChild(eventRow(e)); });
      $("ev-window").textContent = fmt(W.winLine, { w: d.window.label }) + " · " + fmt(W.shown, { n: d.total });
      UI.setStamp($("ev-stamp"), d);
      leftOut($("ev-left"), d, d.items, evt.includeSeen, function (on) { evt.includeSeen = on; loadEvents(); });
      UI.markSeen("event", ids(d.items));
    }).catch(function (e) { UI.fail(tbody, e); });
  }

  // ---------- Suggestions ----------
  function partPills(parts, limit) {
    var box = el("div", { cls: "parts" });
    parts.slice(0, limit || parts.length).forEach(function (p) { box.appendChild(pill(p.label + " +" + p.points, "acc")); });
    return box;
  }
  function evidenceLine(ev) {
    var bits = [];
    if (ev.row) bits.push(fmt(W.dSheetRow, { r: ev.row }));
    if (ev.industry) bits.push(ev.industry);
    if (ev.geo) bits.push(ev.geo);
    return bits.join(" · ");
  }
  function findButton(s, onAnswer) {
    var btn = UI.control("find", W.findBtn, { "data-endpoint": "/api/act", "aria-label": fmt(W.findAria, { c: s.subject }) });
    btn.disabled = !s.can_find_people;
    btn.addEventListener("click", function () {
      UI.run(btn, "/api/act", { action: "find_people", target_ids: [s.account_id] }, onAnswer, null);
    });
    return btn;
  }
  function openButton(s) {
    var isCompany = s.kind === "newco";
    var label = isCompany ? W.openCompanyBtn : W.openPersonBtn;
    return el("button", { cls: "btn", type: "button", text: label, on: { click: function () { (isCompany ? UI.openAccount : UI.openPerson)(isCompany ? s.account_id : s.contact_id); } } });
  }
  function addForm(s, hit, row) {
    var wrap = el("div", { cls: "addform", "data-form": "add" });
    var byRole = {};
    hit.form.fields.forEach(function (f) { byRole[f.role] = f.value; });
    var name = el("input", { maxlength: "200", "aria-label": W.addName, value: byRole.name || "" });
    var role = el("input", { maxlength: "200", "aria-label": W.addRole, value: byRole.designation || "" });
    wrap.appendChild(el("div", { cls: "fixed", text: W.addCompany + ": " + hit.form.company }));
    if (byRole.li_url) wrap.appendChild(el("div", { cls: "fixed", text: W.addLink + ": " + byRole.li_url }));
    if (byRole.industry) wrap.appendChild(el("div", { cls: "fixed", text: W.addIndustry + ": " + byRole.industry }));
    wrap.appendChild(el("label", null, el("span", { text: W.addName }), name));
    wrap.appendChild(el("label", null, el("span", { text: W.addRole }), role));
    wrap.appendChild(el("div", { cls: "it-line muted", text: W.addNote }));
    var add = UI.control("add", W.addGo, { "data-endpoint": "/api/act" });
    var cancel = el("button", { cls: "btn", type: "button", text: W.addCancel, on: { click: function () { wrap.hidden = true; } } });
    add.addEventListener("click", function () { submitAdd(s, hit, { name: name, role: role, add: add, row: row }); });
    wrap.appendChild(el("div", { cls: "it-act" }, add, cancel));
    return wrap;
  }
  // The human's edits are the only free text. Every other cell is the sheet value or the hit.
  function submitAdd(s, hit, f) {
    var typed = f.name.value.trim();
    if (!typed) { UI.calm(W.addNeedName, true); return; }
    var fields = [{ role: "name", value: typed }];
    if (f.role.value.trim()) fields.push({ role: "designation", value: f.role.value.trim() });
    hit.form.fields.forEach(function (c) { if (c.role !== "name" && c.role !== "designation") fields.push({ role: c.role, value: c.value }); });
    var body = { action: "row_add", target_ids: [s.account_id], payload: { fields: fields } };
    // No list reload after Add: a reload would hide the row this person just saw (D75).
    UI.run(f.add, "/api/act", body, function () {
      f.row.classList.add("is-done");
      f.add.textContent = W.addedState;
      f.add.disabled = true;
    }, function () {
      f.row.classList.remove("is-done");
      f.add.textContent = W.addGo;
      f.add.disabled = false;
    });
  }
  function hitRow(s, hit) {
    var row = el("li", { "data-hit": hit.id });
    row.appendChild(el("div", { cls: "it-line" }, UI.link(hit.href, hit.title)));
    var form = addForm(s, hit, row);
    form.hidden = true;
    var open = el("button", { cls: "btn", type: "button", "data-open": "add", text: W.addOpen, on: { click: function () { form.hidden = !form.hidden; } } });
    row.appendChild(el("div", { cls: "it-meta" }, el("span", { text: fmt(W.hitFrom, { e: hit.engine }) }), open));
    row.appendChild(form);
    return row;
  }
  function suggestionRow(s) {
    var row = el("li", { "data-id": s.id, "data-kind": s.kind, "data-seen": s.seen ? "1" : "0", cls: s.seen ? "is-seen" : "" });
    var sub = [s.kind === "newco" ? null : s.company, s.title].filter(Boolean).join(" · ");
    row.appendChild(el("div", { cls: "it-head" }, el("span", { cls: "it-title" }, el("b", { text: s.subject }), sub ? el("span", { cls: "muted", text: " · " + sub }) : null),
      pill(fmt(W.sgScore, { n: s.score }), "ok")));
    row.appendChild(partPills(s.parts));
    if (s.via) row.appendChild(el("div", { cls: "it-line", text: s.via }));
    var evidence = evidenceLine(s.evidence);
    if (evidence) row.appendChild(el("div", { cls: "it-line muted", text: evidence }));
    var acts = el("div", { cls: "it-act" });
    if (s.kind === "newco") {
      var state = el("span", { cls: "muted", "data-field": "search", text: s.search ? s.search.message : "" });
      acts.appendChild(findButton(s, function (answer) { state.textContent = answer.label; }));
      acts.appendChild(state);
    }
    acts.appendChild(openButton(s));
    row.appendChild(acts);
    if (s.kind === "newco") {
      var hits = el("ul", { cls: "hits" });
      if (!s.hits.length) hits.appendChild(el("li", { cls: "muted", text: W.noHits }));
      s.hits.forEach(function (h) { hits.appendChild(hitRow(s, h)); });
      row.appendChild(el("div", { cls: "it-line" }, el("b", { text: W.hitsTitle })));
      row.appendChild(hits);
    }
    return row;
  }
  function loadSuggest(params) {
    if (params && SUGGEST_SEEN[params.tab]) { sug.kind = params.tab; sug.includeSeen = false; }
    UI.tabs($("sg-tabs"), SUGGEST_TABS, sug.kind, function (k) { sug.kind = k; sug.includeSeen = false; sug.offset = 0; loadSuggest(); });
    var mine = ++sug.req;
    var query = "/api/suggest/" + sug.kind + "?limit=" + UI.PAGE + "&offset=" + sug.offset + (sug.includeSeen ? "&include_seen=true" : "");
    UI.api(query).then(function (d) {
      if (mine !== sug.req) return;
      var list = clear($("sg-list"));
      if (!d.items.length) list.appendChild(emptyItem(d.empty));
      unseenFirst(d.items).forEach(function (s) { list.appendChild(suggestionRow(s)); });
      $("sg-window").textContent = fmt(W.winLine, { w: d.window.label }) + " · " + fmt(W.shown, { n: d.total }) + (d.capped ? " · " + d.capped_note : "");
      UI.setStamp($("sg-stamp"), d);
      leftOut($("sg-left"), d, d.items, sug.includeSeen, function (on) { sug.includeSeen = on; loadSuggest(); });
      UI.pager($("sg-pager"), d.total, sug.offset, function (o) { sug.offset = o; loadSuggest(); });
      UI.markSeen(SUGGEST_SEEN[sug.kind], ids(d.items));
    }).catch(function (e) { UI.fail($("sg-list"), e); });
  }

  // ---------- Today: needs a look, Saley suggests ----------
  function lookRow(i) {
    var row = el("li", { "data-kind": i.kind });
    var title = i.href ? UI.link(i.href, i.title) : document.createTextNode(i.title);
    row.appendChild(el("div", { cls: "it-head" }, el("span", { cls: "it-title" }, title),
      el("a", { href: "#" + i.link.view + (i.link.filter ? "?tab=" + encodeURIComponent(i.link.filter) : ""), text: W.lookOpen })));
    var meta = el("div", { cls: "it-meta" }, el("span", { text: i.detail }), el("span", { text: i.at_label }));
    if (i.verify_note) meta.appendChild(pill(W.verifyPill, "warn"));
    row.appendChild(meta);
    return row;
  }
  UI.renderLook = function (block) {
    var list = clear($("look-list"));
    if (!block.items.length) list.appendChild(emptyItem(block.empty));
    block.items.forEach(function (i) { list.appendChild(lookRow(i)); });
    $("look-more").textContent = block.total > block.items.length ? fmt(W.lookMore, { n: block.total }) : "";
    $("look-replies").textContent = block.replies_note || "";
  };
  function todaySuggestion(s) {
    var row = el("li", { "data-kind": s.kind });
    row.appendChild(el("div", { cls: "it-head" }, el("span", { cls: "it-title" }, el("b", { text: s.subject }),
      el("span", { cls: "muted", text: s.kind === "newco" ? "" : " · " + (s.company || "") })), pill(fmt(W.sgScore, { n: s.score }), "ok")));
    row.appendChild(partPills(s.parts, TODAY_PARTS));
    var state = el("span", { cls: "muted", text: s.search ? s.search.message : "" });
    var acts = el("div", { cls: "it-act" });
    if (s.kind === "newco") { acts.appendChild(findButton(s, function (answer) { state.textContent = answer.label; })); acts.appendChild(state); }
    else acts.appendChild(openButton(s));
    row.appendChild(acts);
    return row;
  }
  UI.renderSuggests = function (block) {
    var list = clear($("sug-list"));
    block.items.forEach(function (s) { list.appendChild(todaySuggestion(s)); });
    $("sug-empty").textContent = block.items.length ? "" : block.empty;
  };

  // ---------- Sources and health: the news block ----------
  function funnelRow(step, top, picked) {
    var bar = el("div", { cls: "bar" }, el("i"));
    bar.firstChild.style.width = (step.n && top ? Math.round(100 * step.n / top) : 0) + "%";
    var label = el("span", { cls: step.key === picked ? "hl" : "" }, step.step);
    if (step.note) label.appendChild(el("span", { cls: "muted", text: " · " + step.note }));
    var count = step.n == null
      ? el("span", { cls: "n muted", text: "—" })
      : el("a", { cls: "n", href: step.href, "data-news": step.key, title: W.funnelOpen, text: step.n });
    return el("div", { cls: "row", "data-step": step.key }, label, bar, count);
  }
  function renderFunnel(news, picked) {
    var box = clear($("news-funnel"));
    var top = Math.max.apply(null, news.steps.map(function (s) { return s.n || 0; }));
    news.steps.forEach(function (s) { box.appendChild(funnelRow(s, top, picked)); });
  }
  function renderSources(news, picked) {
    var failed = clear($("news-failed"));
    if (!news.failed_sources.length) failed.appendChild(emptyItem(W.newsNoFailed));
    news.failed_sources.forEach(function (s) {
      failed.appendChild(el("li", { "data-source": s.name }, el("b", { text: s.name }), el("span", { cls: "muted", text: " · " + s.problem })));
    });
    var watch = clear($("news-watch"));
    news.watch.forEach(function (line) { watch.appendChild(el("li", { "data-kind": line.kind, text: line.text })); });
    var tbody = clear($("src-table").querySelector("tbody"));
    var column = FUNNEL_PILL_COLUMN[picked];
    news.sources.forEach(function (s) {
      var cells = [
        el("td", { text: s.name + (s.active ? "" : " · " + W.sOff) }), el("td", { text: s.last_status || W.sNever }),
        el("td", { cls: "num", text: s.runs_24h }), el("td", { cls: "num", text: s.errors_24h }),
        el("td", { cls: "num", text: s.seen_24h }), el("td", { cls: "num", text: s.new_24h })];
      if (column) cells[column].classList.add("hl");
      tbody.appendChild(el("tr", null, cells[0], cells[1], cells[2], cells[3], cells[4], cells[5]));
    });
    var step = news.steps.filter(function (s) { return s.key === picked; })[0];
    $("news-pick").textContent = step ? fmt(W.newsPickNote, { s: step.step }) : "";
  }
  function connectionLines(d) {
    var search = d.search;
    var lines = [d.writes.enabled ? W.connWrites : d.writes.message, d.mail.message, d.notes.message];
    lines.push(fmt(W.searchStatus, { e: search.engine, n: search.requests_today, c: search.daily_cap }));
    if (search.unavailable_until) lines.push(fmt(W.searchUntil, { t: search.unavailable_until }));
    if (search.last_failure) lines.push(search.last_failure);
    lines.push(search.message, d.apollo.message);
    return lines;
  }
  UI.renderNews = function (d, params) {
    var picked = params.news || "";
    renderFunnel(d.news, picked);
    renderSources(d.news, picked);
    var conn = clear($("conn-list"));
    connectionLines(d).forEach(function (line) { conn.appendChild(el("li", { text: line })); });
    if (picked) $("news-sources-card").scrollIntoView();
  };

  UI.loaders.signals = loadSignals;
  UI.loaders.events = loadEvents;
  UI.loaders.suggest = loadSuggest;
  document.addEventListener("DOMContentLoaded", function () {
    var timers = {};
    var typed = function (key, id) {
      $(id).addEventListener("input", function () {
        clearTimeout(timers[id]);
        timers[id] = setTimeout(function () { sig[key] = $(id).value.trim(); sig.offset = 0; fetchSignals(); }, FILTER_MS);
      });
    };
    typed("src", "sig-src");
    typed("entity", "sig-entity");
  });
})();
