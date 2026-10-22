// Is this error a page chunk that did not load? (R5 T17 review.) After a deploy the old chunk
// names are gone, so `import()` of a page rejects. React.lazy keeps that rejection, so only a
// full reload (new index.html, new chunk names) helps. Any other render error can try again.
const CHUNK_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /unable to preload css/i,
];

export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if (error.name === "ChunkLoadError") return true;
  return CHUNK_PATTERNS.some((pattern) => pattern.test(error.message));
}
