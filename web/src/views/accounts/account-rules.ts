// Pure rules of the Accounts page: the filter list (the legacy tabs), the account tags as
// legend tags, and the path of one page of the list. No DOM, no fetch.
import type { FilterOption } from "@/components/saley/FilterBar";
import { PAGE_SIZE } from "@/components/saley/Pager";
import type { Tone } from "@/components/saley/tones";
import { fmt, W } from "@/words";

export const DEFAULT_FILTER = "all";
const GOAL_FILTERS = ["G3", "G5"]; // the industry-rule goals of the legacy page

/** The filters: all, P1, has a connection, the goal filters, and the industry in use. */
export function accountFilters(current: string): FilterOption[] {
  const options: FilterOption[] = [
    { id: "all", label: W.tabAll },
    { id: "p1", label: W.tabP1 },
    { id: "has_connection", label: W.tabConnected },
    ...GOAL_FILTERS.map((g) => ({ id: `goal:${g}`, label: fmt(W.tabGoal, { n: g.slice(1) }) })),
  ];
  if (options.some((o) => o.id === current)) return options;
  if (current.startsWith("industry:")) {
    options.push({ id: current, label: fmt(W.tabIndustry, { s: current.slice(9) }) });
  } else if (/^goal:G\d{1,2}$/.test(current)) {
    options.push({ id: current, label: fmt(W.tabGoal, { n: current.slice(6) }) });
  }
  return options;
}

const TAGS: Record<string, { tag: string; tone: Tone }> = {
  Qualified: { tag: "qualified", tone: "good" },
  Engaged: { tag: "engaged", tone: "info" },
  "Single-threaded": { tag: "single", tone: "warn" },
};

/** An account tag of the API as its legend tag. An unknown tag keeps the neutral tone. */
export function accountTag(name: string): { tag: string; tone: Tone } {
  return TAGS[name] ?? { tag: "dash", tone: "neutral" };
}

export function accountsPath(filter: string, q: string, offset: number): string {
  const parts = [`filter=${encodeURIComponent(filter)}`, `limit=${PAGE_SIZE}`, `offset=${offset}`];
  if (q) parts.push(`q=${encodeURIComponent(q)}`);
  return `/api/accounts?${parts.join("&")}`;
}
