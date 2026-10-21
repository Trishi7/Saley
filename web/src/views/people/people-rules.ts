// Pure rules of the People page: the filters (the legacy tabs, with counts), the label of a
// filter that a link brings (a funnel bar: "Contacted or later"), the last touch line and the
// path of one page of the list. No DOM, no fetch.
import type { FilterOption } from "@/components/saley/FilterBar";
import { PAGE_SIZE } from "@/components/saley/Pager";
import { typeName } from "@/components/saley/TouchTimeline";
import { STAGES } from "@/components/saley/views";
import { dayLabel } from "@/lib/format";
import { fmt, W } from "@/words";
import type { PeopleCounts, TouchBrief } from "./types";

export const DEFAULT_FILTER = "backlog";

const TABS: [keyof PeopleCounts | "all" | "contacted_this_week", string][] = [
  ["backlog", W.fBacklog],
  ["p1_waiting", W.fP1],
  ["in_conversation", W.fConversation],
  ["reconnect_due", W.fReconnect],
  ["contacted_this_week", W.fWeek],
  ["all", W.fAll],
];

/** The Today funnel keys of `reached_<key>` filters, as the legend's stage names. */
const REACHED: Record<string, string> = {
  contacted: "stage-contacted",
  connected: "stage-connected",
  talking: "stage-talking",
  met: "stage-met",
};

function stageName(tag: string): string {
  return STAGES.find((s) => s.tag === tag)?.name ?? tag;
}

/** The label of a filter that is not one of the tabs, or null when it has none. */
export function otherFilterLabel(filter: string): string | null {
  if (filter.startsWith("stage:")) return fmt(W.fStage, { s: filter.slice(6) });
  if (filter.startsWith("reached_") && REACHED[filter.slice(8)]) {
    return fmt(W.fReached, { s: stageName(REACHED[filter.slice(8)]) });
  }
  if (filter.startsWith("account:")) return W.fAccount;
  return null;
}

/** The tabs with their counts ("P1 waiting · 4"); a count the API does not give is left off. */
export function peopleFilters(current: string, counts: PeopleCounts | undefined): FilterOption[] {
  const options: FilterOption[] = TABS.map(([id, label]) => {
    const n = counts && id in counts ? counts[id as keyof PeopleCounts] : undefined;
    return { id, label: n === undefined ? label : fmt(W.tabCount, { label, n }) };
  });
  const other = options.some((o) => o.id === current) ? null : otherFilterLabel(current);
  if (other) options.push({ id: current, label: other });
  return options;
}

/** "Email 2 · Fri 17 Oct" for the newest touch; no touch: null (the cell shows "—"). */
export function lastTouch(touches: TouchBrief[]): string | null {
  const newest = touches[0];
  if (!newest) return null;
  return `${typeName(newest.type)} · ${dayLabel(newest.date)}`;
}

export function peoplePath(filter: string, q: string, offset: number): string {
  const parts = [`filter=${encodeURIComponent(filter)}`, `limit=${PAGE_SIZE}`, `offset=${offset}`];
  if (q) parts.push(`q=${encodeURIComponent(q)}`);
  return `/api/people?${parts.join("&")}`;
}
