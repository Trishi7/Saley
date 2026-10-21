// Pure rules of the Today task list (no DOM, no fetch), so the page logic stays small and
// easy to check. The server decides order, due days and Done values (D152, D153, D180);
// these helpers only choose how a row looks.
import type { Tone } from "@/components/saley/tones";
import { W } from "@/words";
import type { ActAnswer, Task } from "./types";

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;

/** Today's date in IST as "YYYY-MM-DD" (docs/08: every date is an IST day). */
export function istToday(now: number = Date.now()): string {
  return new Date(now + IST_OFFSET_MS).toISOString().slice(0, 10);
}

export type DueTag = { tag: string; tone: Tone };

/** The due tag of a row: late, due today, due later, or no date (D180). Never "overdue". */
export function dueTag(due: string | null, today: string): DueTag {
  if (!due) return { tag: "no-date", tone: "neutral" };
  const day = due.slice(0, 10);
  if (day < today) return { tag: "late", tone: "bad" };
  if (day === today) return { tag: "due-today", tone: "warn" };
  return { tag: "due-later", tone: "neutral" };
}

export type KindTag = { tag: string; tone: Tone; name: string };

const KIND_TAGS: Record<string, KindTag> = {
  meeting: { tag: "task-meeting", tone: "touch-meet", name: W.kindMeeting },
  reconnect: { tag: "task-reconnect", tone: "info", name: W.kindReconnect },
  email: { tag: "task-email", tone: "touch-email", name: W.kindEmail },
  linkedin: { tag: "task-li", tone: "touch-li", name: W.kindLi },
  commitment: { tag: "task-promise", tone: "touch-other", name: W.kindPromise },
  team: { tag: "task-team", tone: "primary", name: W.kindTeam },
};

const KIND_GROUP: Record<string, keyof typeof KIND_TAGS> = {
  meeting: "meeting",
  status_check: "meeting",
  reconnect: "reconnect",
  add_email: "email",
  email1: "email",
  email2: "email",
  email3: "email",
  li_confirm: "linkedin",
  li_first: "linkedin",
  commitment: "commitment",
  decision: "team",
  review_write: "team",
  inbox_cards: "team",
};

/** The kind tag of a task (importance groups of D152). An unknown kind is a team task. */
export function kindTag(kind: string): KindTag {
  return KIND_TAGS[KIND_GROUP[kind] ?? "team"];
}

/** A Done this page made: the write to undo and when the Undo ends. */
export type Tick = { writeId: number; until: number };

/** The tick a Done answer gives, or null when nothing new was written (already done). */
export function tickFrom(answer: ActAnswer): Tick | null {
  const fresh = answer.items.find((i) => !i.already);
  const until = answer.undo_until ? Date.parse(answer.undo_until) : NaN;
  if (!fresh || Number.isNaN(until)) return null;
  return { writeId: answer.write_id, until };
}

/** The tick of a row: this page's own Done first, else the server's (a reload keeps Undo).
 *  `local` null means this page undid it: the server's older answer no longer counts. */
export function tickOf(task: Task, local: Tick | null | undefined): Tick | null {
  if (local !== undefined) return local;
  if (task.done && task.undo_id !== null && task.undo_until) {
    const until = Date.parse(task.undo_until);
    return Number.isNaN(until) ? null : { writeId: task.undo_id, until };
  }
  return null;
}

/** What the row's control is: Undo, Done, Add email, a link, a closed tick, or nothing. */
export type RowControl = "undo" | "closed" | "done" | "add_email" | "link" | "none";

export function rowControl(task: Task, tick: Tick | null, now: number): RowControl {
  if (tick) return tick.until > now ? "undo" : "closed";
  if (task.done && !task.action) return "closed";
  if (task.input_role === "email") return "add_email"; // D154: no Done on Add email
  if (task.action) return "done";
  if (task.link) return "link";
  return "none";
}
