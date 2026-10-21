// Pure rules of the team pages (R5 T16a: Inbox, Commitments, Decisions, Weekly pack, Scorecard,
// Sources and health). No DOM, no fetch, no imports, so `npm test` (node --test) runs them as
// they are. The server decides every value; these helpers only choose how a row looks.

/** A signed write this page made: the write to undo and when the Undo ends (epoch ms). */
export type Tick = { writeId: number; until: number };

/** The Undo of a POST answer (`write_id`, `undo_until`, `already`), or null when nothing new
 *  was written (`already`) or the answer has no Undo window. */
export function tickFromAnswer(answer: {
  write_id?: number | null;
  undo_until?: string | null;
  already?: boolean;
}): Tick | null {
  if (answer.already || answer.write_id == null || !answer.undo_until) return null;
  const until = Date.parse(answer.undo_until);
  return Number.isNaN(until) ? null : { writeId: answer.write_id, until };
}

/** The tick of a row: this page's own press first, else the server's open Undo (a reload
 *  keeps the Undo in place, as Today). `local` null = undone here: the server's older
 *  answer no longer counts until the list loads again. */
export function rowTick(
  server: { undo_id?: number | null; undo_until?: string | null },
  local: Tick | null | undefined,
): Tick | null {
  if (local !== undefined) return local;
  if (server.undo_id == null || !server.undo_until) return null;
  const until = Date.parse(server.undo_until);
  return Number.isNaN(until) ? null : { writeId: server.undo_id, until };
}

/** What a row's control is: its act (Done, Do it), Undo in place, or a closed tick. */
export type Control = "act" | "undo" | "closed";

export function controlOf(tick: Tick | null | undefined, now: number): Control {
  if (!tick) return "act";
  return tick.until > now ? "undo" : "closed";
}

/** When the next open Undo window ends (Infinity when none is open). */
export function nextWake(ticks: Iterable<Tick | null | undefined>, now: number): number {
  let next = Infinity;
  for (const tick of ticks) {
    if (tick && tick.until > now && tick.until < next) next = tick.until;
  }
  return next;
}

const CARD_ID = /^\/api\/proposal\/(\d+)\/dismiss$/;

type CardLike = {
  kind: string;
  dismiss: string | null;
  action: { body: { payload?: Record<string, unknown> | null } | null } | null;
};

/** The draft of an action card that can be edited: only a card with ONE draft cell (D129,
 *  D186). The proposal id comes from the card's own dismiss path, never from a guess. */
export function draftOf(item: CardLike): { id: number; text: string } | null {
  const match = item.kind === "card" && item.dismiss ? CARD_ID.exec(item.dismiss) : null;
  const cells = item.action?.body?.payload?.cells;
  if (!match || !Array.isArray(cells) || cells.length !== 1) return null;
  const value = (cells[0] as { value?: unknown } | null)?.value;
  if (typeof value !== "string") return null;
  return { id: Number(match[1]), text: value };
}

/** Up or down against last week. Equal, or a value that is not known, has no trend. */
export function trend(
  value: number | null | undefined,
  last: number | null | undefined,
): "up" | "down" | null {
  if (value == null || last == null || value === last) return null;
  return value > last ? "up" : "down";
}

/** The sum of a column, or null when the column is empty or one value is not known: a total
 *  with a hole would be a made-up number (docs/08: unknown is "—", never 0). */
export function sumKnown(values: readonly (number | null | undefined)[]): number | null {
  if (values.length === 0) return null;
  let total = 0;
  for (const value of values) {
    if (value == null) return null;
    total += value;
  }
  return total;
}

/** "2026-10-19" plus `weeks` weeks, as "YYYY-MM-DD" (UTC dates: no time zone shift). */
export function shiftWeek(iso: string, weeks: number): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const day = new Date(Date.UTC(y, m - 1, d + 7 * weeks));
  return day.toISOString().slice(0, 10);
}

/** True for a Monday (the pack key, R4 D117). A broken date is not a Monday. */
export function isMonday(iso: string): boolean {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return false;
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay() === 1;
}

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;

/** An ISO time as "HH:MM" in IST (docs/08: every time is IST). A broken time gives "—". */
export function istTime(iso: string | null | undefined): string {
  const at = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(at)) return "—";
  return new Date(at + IST_OFFSET_MS).toISOString().slice(11, 16);
}

/** One page of a list the page already holds (the pack body, the health tables). */
export function pageOf<T>(items: readonly T[], offset: number, size = 10): T[] {
  const start = Math.max(0, Math.min(offset, Math.max(0, items.length - 1)));
  return items.slice(start - (start % size), start - (start % size) + size);
}

/** The "Run news now" state (D150, D178): the Button is off while a run is queued or running. */
export type NewsRun = { busy: boolean; state: string | null };

export function newsRunOf(answer: { open: boolean; latest: { state: string } | null } | undefined): NewsRun {
  if (!answer) return { busy: true, state: null }; // not known yet: no press before the state
  return { busy: answer.open, state: answer.latest?.state ?? null };
}
