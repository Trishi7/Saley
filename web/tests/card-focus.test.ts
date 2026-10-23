// `npm test`: which element a Discord deep link outlines (src/lib/card-focus.ts; R7, D261).
import assert from "node:assert/strict";
import { test } from "node:test";
import { focusSelector, openId } from "../src/lib/card-focus.ts";

test("a link with an item outlines the open drawer or the item's row", () => {
  const sel = focusSelector("m5-c412-20261024", "412");
  assert.ok(sel?.includes('[data-person="412"]'));
  assert.ok(sel?.includes("[data-drawer].on"));
});

test("a link without an item outlines the page content", () => {
  assert.equal(focusSelector("m1-sid-20261023"), "[data-content]");
});

test("no card id, or a broken one, outlines nothing and never builds a selector from it", () => {
  assert.equal(focusSelector(undefined, "412"), null);
  assert.equal(focusSelector('x"],[onclick="1'), null);
  assert.equal(focusSelector("m5-x", '412"],body,["'), "[data-content]");
});

test("only a plain number opens a drawer", () => {
  assert.equal(openId("412"), 412);
  assert.equal(openId("2026-W43"), null);
  assert.equal(openId(undefined), null);
});
