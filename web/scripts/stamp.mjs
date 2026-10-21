// Build stamp (D145). Runs after `vite build` and writes dist/build-stamp.json with a sha256
// over the build inputs. Gate step 1 computes the same hash in Python
// (src/saley/qa/build_stamp.py) and fails when the dist is older than the sources.
// The hash rule (both sides must match): files in INPUTS plus every file under src/, sorted
// by their path relative to web/ (with "/"), each fed as path, NUL, content with CRLF turned
// into LF, NUL. CRLF and LF checkouts of the same commit give the same stamp.
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const WEB = path.resolve(import.meta.dirname, "..");
const DIST = path.resolve(WEB, "../src/saley/web/dist");
// components.json, .npmrc, .nvmrc and eslint.config.js do not change the built output, so they
// are not inputs. The stamp is written only by `npm run build`, after `vite build`.
const INPUTS = [
  "index.html",
  "package.json",
  "package-lock.json",
  "public/theme-init.js",
  "tsconfig.json",
  "vite.config.ts",
];

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : entry.isFile() ? [full] : [];
  });
}

export function sourceHash() {
  const files = [
    ...INPUTS.map((name) => path.join(WEB, name)).filter((f) => existsSync(f)),
    ...walk(path.join(WEB, "src")),
  ];
  const rel = files.map((f) => path.relative(WEB, f).split(path.sep).join("/")).sort();
  const hash = createHash("sha256");
  for (const name of rel) {
    const body = readFileSync(path.join(WEB, name)).toString("latin1").replaceAll("\r\n", "\n");
    hash.update(name, "utf8");
    hash.update("\0");
    hash.update(Buffer.from(body, "latin1"));
    hash.update("\0");
  }
  return { sha256: hash.digest("hex"), files: rel.length };
}

const stamp = { ...sourceHash(), built_at: new Date().toISOString() };
writeFileSync(path.join(DIST, "build-stamp.json"), `${JSON.stringify(stamp, null, 2)}\n`);
console.log(`build stamp ${stamp.sha256.slice(0, 12)} over ${stamp.files} files`);
