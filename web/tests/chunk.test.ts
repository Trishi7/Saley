// `npm test`: which page errors need a reload (src/lib/chunk.ts; R5 T17 review).
import assert from "node:assert/strict";
import { test } from "node:test";
import { isChunkLoadError } from "../src/lib/chunk.ts";

test("a page chunk that did not load needs a reload, in each browser's words", () => {
  assert.ok(isChunkLoadError(new TypeError("Failed to fetch dynamically imported module: /static/app/assets/TodayPage-x.js")));
  assert.ok(isChunkLoadError(new TypeError("error loading dynamically imported module")));
  assert.ok(isChunkLoadError(new TypeError("Importing a module script failed.")));
  const named = new Error("x");
  named.name = "ChunkLoadError";
  assert.ok(isChunkLoadError(named));
});

test("any other error, or a thrown non-error, is not a chunk error", () => {
  assert.equal(isChunkLoadError(new Error("Cannot read properties of undefined")), false);
  assert.equal(isChunkLoadError("Failed to fetch dynamically imported module"), false);
  assert.equal(isChunkLoadError(null), false);
});
