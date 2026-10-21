// The /api/inbox answer as the Inbox reads it (src/saley/web/schemas.py InboxOut, mine.py).
import type { Tone } from "@/components/saley/tones";
import { W } from "@/words";

export type InboxKind = "card" | "confirm" | "ask" | "reminder";

export type InboxAction = {
  endpoint: string;
  body: {
    action: string;
    target_ids: number[];
    payload?: Record<string, unknown> | null;
  } | null;
};

export type InboxItem = {
  kind: InboxKind;
  id: number;
  type: string;
  title: string;
  why: string | null;
  person: string | null;
  for_user: string | null;
  at: string | null;
  button: string | null;
  action: InboxAction | null;
  dismiss: string | null;
  evidence: { row: string | null };
};

export type InboxData = {
  items: InboxItem[];
  total: number;
  empty: string;
  last_computed_label: string;
};

/** The answer of a write from the Inbox: /api/act (ActOut), the card edit (ProposalEditOut)
 *  or a confirm (ConfirmOut, no Undo). */
export type WriteAnswer = {
  ok: boolean;
  already: boolean;
  write_id?: number | null;
  undo_until?: string | null;
  label?: string;
};

/** The kinds in filter order, each with its legend tag (views.ts, view "asks"). */
export const INBOX_KINDS: { kind: InboxKind; name: string; tone: Tone }[] = [
  { kind: "card", name: W.inboxCards, tone: "primary" },
  { kind: "confirm", name: W.inboxConfirms, tone: "info" },
  { kind: "ask", name: W.inboxAsks, tone: "neutral" },
  { kind: "reminder", name: W.inboxReminders, tone: "warn" },
];

export const KIND_TAG: Record<InboxKind, { name: string; tone: Tone }> = {
  card: { name: W.lgCard, tone: "primary" },
  confirm: { name: W.lgConfirm, tone: "info" },
  ask: { name: W.lgAsk, tone: "neutral" },
  reminder: { name: W.lgReminder, tone: "warn" },
};
