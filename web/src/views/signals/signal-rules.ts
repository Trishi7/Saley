// Signals: the filters and the query (R5 T15). Pure functions, no DOM.
// D156, D239: categories are the seven real names (`cat=Hiring`), never the A–L source letters. "Other"
// is a topic but never a chip. The filters live in the hash (`#signals?tab=linked&cat=Funding`),
// so a link from the news funnel or Today opens the same list (legacy params kept: tab, range,
// day, min_importance, linked_only).
import type { Tone } from "@/components/saley/tones";
import { fmt, W } from "@/words";

export type Category = { id: string; label: string; tag: string; tone: Tone };

/** The seven names in the API's spelling (saley.news.topic.TOPIC_NAMES). */
export const CATEGORIES: Category[] = [
  { id: "Hiring", label: W.catHiring, tag: "cat-hiring", tone: "cat-1" },
  { id: "Funding", label: W.catFunding, tag: "cat-funding", tone: "cat-2" },
  { id: "Product launch", label: W.catLaunch, tag: "cat-launch", tone: "cat-3" },
  { id: "Research", label: W.catResearch, tag: "cat-research", tone: "cat-4" },
  { id: "Partnership", label: W.catPartner, tag: "cat-partner", tone: "cat-5" },
  { id: "Opportunity", label: W.catOpportunity, tag: "cat-opportunity", tone: "cat-7" },
  { id: "Event", label: W.catEvent, tag: "cat-event", tone: "cat-6" },
];
export const ALL = "all";

export const TABS = [
  { id: "all", label: W.sigTabAll },
  { id: "linked", label: W.sigTabLinked },
];
export const RANGES = [
  { id: "day", label: W.rangeDay },
  { id: "week", label: W.rangeWeek },
  { id: "month", label: W.rangeMonth },
  { id: "year", label: W.rangeYear },
];
/** The API's `tag` values (signals.Tag), without "Ours" (that is the Ours tag on a row). */
export const NEWS_TAGS = ["Opportunity", "People", "Risk", "Market", "Tender", "Discover"];

export type SignalFilters = {
  tab: "all" | "linked";
  range: string;
  day: string;
  cat: string;
  tag: string;
  minImportance: string;
  linkedOnly: boolean;
};

const DEFAULT_RANGE = "week";
const IMPORTANT = 4; // importance 4 or 5 of 5 gets the "Important" tag (D158 uses the same bar)

/** Hash params -> filters. An unknown value falls back to the default, never to a 422. */
export function filtersFrom(params: Record<string, string>): SignalFilters {
  const known = (list: { id: string }[], value: string | undefined, fallback: string) =>
    list.some((o) => o.id === value) ? (value as string) : fallback;
  return {
    tab: params.tab === "linked" ? "linked" : "all",
    range: known(RANGES, params.range, DEFAULT_RANGE),
    day: /^\d{4}-\d{2}-\d{2}$/.test(params.day ?? "") ? params.day : "",
    cat: known(CATEGORIES, params.cat, ""),
    tag: NEWS_TAGS.includes(params.tag ?? "") ? params.tag : "",
    minImportance: /^[1-5]$/.test(params.min_importance ?? "") ? params.min_importance : "",
    linkedOnly: params.linked_only === "true",
  };
}

/** Filters -> hash params (only the ones that differ from the default). */
export function paramsFor(f: SignalFilters): Record<string, string> {
  const out: Record<string, string> = {};
  if (f.tab !== "all") out.tab = f.tab;
  if (f.range !== DEFAULT_RANGE) out.range = f.range;
  if (f.day) out.day = f.day;
  if (f.cat) out.cat = f.cat;
  if (f.tag) out.tag = f.tag;
  if (f.minImportance) out.min_importance = f.minImportance;
  if (f.linkedOnly) out.linked_only = "true";
  return out;
}

/** The list query without view/limit/offset (useSeenList adds them). Text is encoded. */
export function signalsBase(f: SignalFilters, src: string, entity: string): string {
  const q = [`tab=${f.tab}`, `range=${f.range}`];
  const add = (key: string, value: string) => {
    if (value) q.push(`${key}=${encodeURIComponent(value)}`);
  };
  add("day", f.day);
  add("cat", f.cat);
  add("tag", f.tag);
  add("src", src);
  if (f.tab === "linked") add("entity", entity);
  add("min_importance", f.minImportance);
  if (f.linkedOnly && f.tab === "all") q.push("linked_only=true");
  return `/api/signals?${q.join("&")}`;
}

/** The seen type of the list (signals.SEEN_TYPE): a story on All, a signal on Linked. */
export function seenType(tab: "all" | "linked"): string {
  return tab === "all" ? "story" : "signal";
}

export function categoryOf(name: string | undefined): Category | undefined {
  return CATEGORIES.find((c) => c.id === name);
}

export function isImportant(importance: number | null): boolean {
  return importance !== null && importance >= IMPORTANT;
}

/** "a, b, c +2 more outlets", or the source when there are no outlets. */
export function outletsText(outlets: string[], source: string | null, shown = 3): string {
  if (outlets.length === 0) return source ?? "";
  const head = outlets.slice(0, shown).join(", ");
  const more = outlets.length - shown;
  return more > 0 ? `${head} ${fmt(W.outletsMore, { n: more })}` : head;
}
