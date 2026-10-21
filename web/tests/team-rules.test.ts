// `npm test`: the pure rules of the team pages (src/lib/team-rules.ts; R5 T16a).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  controlOf,
  draftOf,
  isMonday,
  istTime,
  newsRunOf,
  nextWake,
  pageOf,
  rowTick,
  shiftWeek,
  sumKnown,
  tickFromAnswer,
  trend,
} from "../src/lib/team-rules.ts";

test("a fresh signed write gives an Undo; an old one or no window gives none", () => {
  const until = "2026-10-21T10:05:00+00:00";
  assert.deepEqual(tickFromAnswer({ write_id: 7, undo_until: until, already: false }), {
    writeId: 7,
    until: Date.parse(until),
  });
  assert.equal(tickFromAnswer({ write_id: 7, undo_until: until, already: true }), null);
  assert.equal(tickFromAnswer({ write_id: null, undo_until: until }), null);
  assert.equal(tickFromAnswer({ write_id: 7, undo_until: null }), null);
  assert.equal(tickFromAnswer({ write_id: 7, undo_until: "not a time" }), null);
});

test("Done turns into Undo in place until the window ends, then a closed tick", () => {
  assert.equal(controlOf(null, 1000), "act");
  assert.equal(controlOf({ writeId: 1, until: 2000 }, 1000), "undo");
  assert.equal(controlOf({ writeId: 1, until: 2000 }, 2000), "closed");
  assert.equal(nextWake([null, { writeId: 1, until: 900 }, { writeId: 2, until: 3000 }], 1000), 3000);
  assert.equal(nextWake([undefined], 1000), Infinity);
});

const card = (cells: unknown[], dismiss: string | null = "/api/proposal/12/dismiss") => ({
  kind: "card",
  dismiss,
  action: { body: { payload: { cells, proposal_id: 12 } } },
});

test("only a card with one draft cell can be edited (D186)", () => {
  assert.deepEqual(draftOf(card([{ role: "meeting_status", value: "Completed" }])), {
    id: 12,
    text: "Completed",
  });
  assert.equal(draftOf(card([{ value: "a" }, { value: "b" }])), null);
  assert.equal(draftOf(card([])), null);
  assert.equal(draftOf(card([{ value: 3 }])), null);
  assert.equal(draftOf(card([{ value: "a" }], null)), null);
  assert.equal(draftOf(card([{ value: "a" }], "/api/proposal/x/dismiss")), null);
  assert.equal(draftOf({ ...card([{ value: "a" }]), kind: "confirm" }), null);
  assert.equal(draftOf({ kind: "card", dismiss: "/api/proposal/1/dismiss", action: null }), null);
});

test("trend and totals never invent a number", () => {
  assert.equal(trend(5, 3), "up");
  assert.equal(trend(2, 3), "down");
  assert.equal(trend(3, 3), null);
  assert.equal(trend(null, 3), null);
  assert.equal(trend(3, undefined), null);
  assert.equal(sumKnown([1, 2, 3]), 6);
  assert.equal(sumKnown([1, null, 3]), null);
  assert.equal(sumKnown([]), null);
  assert.equal(sumKnown([0, 0]), 0);
});

test("weeks move by Mondays, across months and years", () => {
  assert.equal(shiftWeek("2026-10-19", -1), "2026-10-12");
  assert.equal(shiftWeek("2026-12-28", 1), "2027-01-04");
  assert.equal(shiftWeek("2026-03-02", -1), "2026-02-23");
  assert.equal(isMonday("2026-10-19"), true);
  assert.equal(isMonday("2026-10-20"), false);
  assert.equal(isMonday("bad"), false);
});

test("times are IST HH:MM, a broken time is the dash", () => {
  assert.equal(istTime("2026-10-21T08:30:00+00:00"), "14:00");
  assert.equal(istTime("2026-10-21T20:00:00Z"), "01:30");
  assert.equal(istTime(null), "—");
  assert.equal(istTime("nope"), "—");
});

test("a held list pages by 10 and an offset past the end shows the last page", () => {
  const items = Array.from({ length: 23 }, (_, i) => i);
  assert.deepEqual(pageOf(items, 0), items.slice(0, 10));
  assert.deepEqual(pageOf(items, 10), items.slice(10, 20));
  assert.deepEqual(pageOf(items, 40), items.slice(20, 23));
  assert.deepEqual(pageOf([], 10), []);
});

test("Run news now is off until the state is known, and while a run is open", () => {
  assert.deepEqual(newsRunOf(undefined), { busy: true, state: null });
  assert.deepEqual(newsRunOf({ open: false, latest: null }), { busy: false, state: null });
  assert.deepEqual(newsRunOf({ open: true, latest: { state: "queued" } }), {
    busy: true,
    state: "queued",
  });
  assert.deepEqual(newsRunOf({ open: false, latest: { state: "done" } }), {
    busy: false,
    state: "done",
  });
});

test("a reload keeps the server's Undo; a local press or undo wins", () => {
  const until = "2026-10-21T10:05:00+00:00";
  const server = { undo_id: 9, undo_until: until };
  assert.deepEqual(rowTick(server, undefined), { writeId: 9, until: Date.parse(until) });
  assert.equal(rowTick(server, null), null);
  assert.deepEqual(rowTick(server, { writeId: 3, until: 5 }), { writeId: 3, until: 5 });
  assert.equal(rowTick({ undo_id: null, undo_until: null }, undefined), null);
  assert.equal(rowTick({ undo_id: 9, undo_until: "bad" }, undefined), null);
});
