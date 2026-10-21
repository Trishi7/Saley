// Hash routing, as the legacy app (static/app.js parseHash/go): `#people?filter=p1`.
// The hash keeps the legacy links working after the cut-over (plan §3.1, D147).

export type Route = { view: string; params: Record<string, string> };

const DEFAULT_VIEW = "today";

/** "#people?filter=all" -> {view: "people", params: {filter: "all"}}. A bad part is skipped. */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  const [view, query = ""] = raw.split("?");
  const params: Record<string, string> = {};
  for (const pair of query.split("&")) {
    if (!pair) continue;
    const at = pair.indexOf("=");
    try {
      if (at < 0) params[decodeURIComponent(pair)] = "";
      else params[decodeURIComponent(pair.slice(0, at))] = decodeURIComponent(pair.slice(at + 1));
    } catch {
      // A broken escape in a pasted link: skip that part only.
    }
  }
  return { view: view || DEFAULT_VIEW, params };
}

export function hashFor(view: string, params?: Record<string, string>): string {
  const query = Object.entries(params ?? {})
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join("&");
  return `#${view}${query ? `?${query}` : ""}`;
}

export function go(view: string, params?: Record<string, string>): void {
  window.location.hash = hashFor(view, params);
}
