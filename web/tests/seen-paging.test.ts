// `npm test`: the pure New-tab paging rules (src/lib/seen-paging.ts; R5 T15 review).
import assert from "node:assert/strict";
import { test } from "node:test";
import { afterMark, FRESH, markWithRetry, moveNew, newOffset } from "../src/lib/seen-paging.ts";

test("while marks work, New always asks offset 0 and Next refetches from 0", () => {
  assert.equal(newOffset(FRESH), 0);
  assert.deepEqual(afterMark(FRESH, true), FRESH);
  assert.deepEqual(moveNew(FRESH, 10), FRESH);
});

test("a failed mark switches this visit to offset paging, both ways, never below 0", () => {
  const failed = afterMark(FRESH, false);
  assert.deepEqual(failed, { failed: true, offset: 0 });
  const next = moveNew(failed, 10);
  assert.equal(newOffset(next), 10);
  assert.equal(newOffset(moveNew(next, 0)), 0);
  assert.equal(newOffset(moveNew(next, -10)), 0);
  assert.deepEqual(afterMark(next, true), next); // a later good mark keeps offset paging
});

test("a mark is tried twice at most and reports the result", async () => {
  let calls = 0;
  const flaky = async () => {
    calls += 1;
    if (calls === 1) throw new Error("lost");
  };
  assert.equal(await markWithRetry(flaky), true);
  assert.equal(calls, 2);
  calls = 0;
  const down = async () => {
    calls += 1;
    throw new Error("down");
  };
  assert.equal(await markWithRetry(down), false);
  assert.equal(calls, 2);
});
