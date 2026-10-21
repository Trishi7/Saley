// Pure rules of the Q4 goals page: the state tag, the days-left line, the late tag of a
// blocker and the owner name. No DOM, no fetch. Never the word "overdue" (CLAUDE.md).
import type { Tone } from "@/components/saley/tones";
import type { TeamMember } from "@/hooks/use-me";
import { fmt, W } from "@/words";
import type { Progress } from "@/components/saley/Ring";
import type { Goal } from "./types";

/** Rows shown before "+N more" (one card stays one screen tall on a laptop). */
export const PEOPLE_SHOWN = 4;
export const BLOCKERS_SHOWN = 3;

export type TagSpec = { tag: string; tone: Tone; text: string };

const RAG: Record<string, TagSpec> = {
  "on track": { tag: "on-track", tone: "good", text: W.lgOnTrack },
  "at risk": { tag: "at-risk", tone: "warn", text: W.lgAtRisk },
  blocked: { tag: "blocked", tone: "bad", text: W.lgBlocked },
};

/** The goal state as a legend tag. An unknown state shows as it is, in the neutral tone. */
export function ragTag(rag: string): TagSpec {
  return RAG[rag] ?? { tag: "dash", tone: "neutral", text: rag || "—" };
}

/** "12 days left", "1 day left", "Due today", "The due day is past"; no date: "—". */
export function daysLeftText(days: number | null): string {
  if (days === null) return "—";
  if (days > 1) return fmt(W.daysLeft, { n: days });
  if (days === 1) return W.dayLeft;
  if (days === 0) return W.dueToday;
  return W.duePast;
}

/** A blocker's due state as a legend tag (Late, Due today, Due later). Unknown: null. */
export function lateTag(late: number | null): TagSpec | null {
  if (late === null) return null;
  if (late > 0) {
    return { tag: "late", tone: "bad", text: late === 1 ? W.lateDay : fmt(W.lateDays, { n: late }) };
  }
  if (late === 0) return { tag: "due-today", tone: "warn", text: W.dueToday };
  const ahead = -late;
  return {
    tag: "due-later",
    tone: "neutral",
    text: ahead === 1 ? W.dueTomorrow : fmt(W.dueInDays, { n: ahead }),
  };
}

/** The team member's name for a sheet short code; an unknown code stays as it is. */
export function ownerName(short: string | null, people: TeamMember[] | undefined): string {
  if (!short) return "—";
  const found = (people ?? []).find((p) => p.short.toLowerCase() === short.toLowerCase());
  return found ? found.name : short;
}

/** The ring of a goal; an answer without `progress` shows "—" with the won count (D159). */
export function goalProgress(goal: Goal): Progress {
  return goal.progress ?? { won: goal.won, need: null, pct: null };
}

/** Rows to show: all when open, else the first `limit`. `hidden` is what "+N more" counts. */
export function visibleRows<T>(rows: T[], limit: number, open: boolean): { shown: T[]; hidden: number } {
  const hidden = Math.max(0, rows.length - limit);
  return { shown: open ? rows : rows.slice(0, limit), hidden };
}
